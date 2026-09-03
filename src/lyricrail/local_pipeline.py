from __future__ import annotations

import gc
import hashlib
import json
import math
import os
import queue
import re
import shutil
import statistics
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable

from .config import load_json, load_project_config, resolve_environment_path
from .job import atomic_write_json, replace_unpaired_surrogates
from .lyric_input import normalize_authoritative_lyrics
from .package_manifest import build_package_request, build_release_metadata
from .runner import StageContext, StageHandler
from .song_alignment import (
    VietnameseSongAligner,
    _snapshot_path,
    force_align_full_song_lines,
    get_vietnamese_song_aligner,
)


PUNCTUATION_BREAK = re.compile(r"[.!?…,:;]$")
LIGHTWEIGHT_REFLOW_EVIDENCE = (
    "authoritative-punctuation+aligned-acoustic-pauses+"
    "underthesea-pos+curated-lexical-units"
)
_DLL_DIRECTORY_HANDLES: list[Any] = []
_SEPARATION_MODEL_CACHE: dict[tuple[str, str, str, int, bool], Any] = {}


def _prepend_process_path(directory: Path) -> None:
    normalized = os.path.normcase(str(directory.resolve()))
    entries = [
        os.path.normcase(item)
        for item in os.environ.get("PATH", "").split(os.pathsep)
        if item
    ]
    if normalized not in entries:
        os.environ["PATH"] = str(directory.resolve()) + os.pathsep + os.environ.get(
            "PATH", ""
        )


def _prepare_cuda_runtime() -> bool:
    """Expose PyTorch's bundled CUDA/cuDNN DLLs to ONNX and CTranslate2."""
    try:
        import torch
    except ImportError:
        return False
    torch_lib = Path(torch.__file__).resolve().parent / "lib"
    if os.name == "nt" and torch_lib.is_dir():
        _prepend_process_path(torch_lib)
        add_directory = getattr(os, "add_dll_directory", None)
        if add_directory is not None:
            _DLL_DIRECTORY_HANDLES.append(add_directory(str(torch_lib)))
    return bool(torch.cuda.is_available())


def _project_root(context: StageContext) -> Path:
    configured = str(_job(context).get("runtime", {}).get("projectRoot") or "").strip()
    return Path(configured).resolve() if configured else context.job_directory.parent.parent


def _shared_work(context: StageContext) -> Path:
    directory = context.job_directory / "work" / "shared"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _job(context: StageContext) -> dict[str, Any]:
    return context.store.load(context.job_id)


def _source_media(context: StageContext) -> Path:
    request = _job(context)["request"]
    return Path(request.get("sourceMedia") or request["sourceVideo"])


def _landscape_video(context: StageContext) -> Path:
    return _shared_work(context) / "landscape.mp4"


def _thumbnail(context: StageContext) -> Path:
    return context.artifacts_directory / "thumbnail.webp"


def _thumbnail_base(context: StageContext) -> Path:
    return context.artifacts_directory / "thumbnail-base.webp"


def _player_video(context: StageContext) -> Path:
    return context.artifacts_directory / "playback-video.mp4"


def _player_karaoke_audio(context: StageContext) -> Path:
    return context.artifacts_directory / "karaoke.m4a"


def _original_audio_delivery_plan(probe_data: dict[str, Any]) -> dict[str, Any]:
    """Choose the smallest delivery that does not re-encode source audio.

    AAC and MP3 are the two source codecs that all supported desktop WebViews
    can consume through our authenticated media protocol.  When the requested
    timeline covers the complete source, FFmpeg can remux their packets without
    decoding or re-encoding.  Trims and other codecs use the conservative AAC
    fallback so packet-boundary cuts and platform codec gaps cannot desync the
    Player.
    """
    probe = probe_data.get("lyricRail", {})
    audio_stream = next(
        (
            stream
            for stream in probe_data.get("streams", [])
            if stream.get("codec_type") == "audio"
        ),
        {},
    )
    source_codec = str(audio_stream.get("codec_name") or "").strip().lower()
    source_profile = str(audio_stream.get("profile") or "").strip()
    source_duration = float(probe_data.get("format", {}).get("duration") or 0.0)
    trim_start = float(probe.get("trimStartSeconds") or 0.0)
    trim_end = float(probe.get("trimEndSeconds") or 0.0)
    duration_tolerance = max(0.01, source_duration * 0.000001)
    full_timeline = (
        source_duration > 0.0
        and trim_start <= 0.001
        and abs(trim_end - source_duration) <= duration_tolerance
    )

    common = {
        "sourceCodec": source_codec or "unknown",
        "sourceProfile": source_profile,
        "fullSourceTimeline": full_timeline,
    }
    if full_timeline and source_codec == "aac":
        return {
            **common,
            "mode": "bitstream-copy",
            "outputSuffix": ".m4a",
            "mediaType": "audio/mp4",
            "container": "m4a",
            "transcodeOccurred": False,
            "qualityPreservation": "encoded-audio-payload-preserved",
            "reason": "Complete AAC timeline can be remuxed without re-encoding.",
        }
    if full_timeline and source_codec == "mp3":
        return {
            **common,
            "mode": "bitstream-copy",
            "outputSuffix": ".mp3",
            "mediaType": "audio/mpeg",
            "container": "mp3",
            "transcodeOccurred": False,
            "qualityPreservation": "encoded-audio-payload-preserved",
            "reason": "Complete MP3 timeline can be remuxed without re-encoding.",
        }
    reason = (
        "A trimmed timeline requires sample-accurate decoding and encoding."
        if not full_timeline
        else f"Source codec {source_codec or 'unknown'} is not in the portable copy set."
    )
    return {
        **common,
        "mode": "aac-fallback",
        "outputSuffix": ".m4a",
        "mediaType": "audio/mp4",
        "container": "m4a",
        "transcodeOccurred": True,
        "qualityPreservation": "high-quality-portable-transcode",
        "reason": reason,
    }


def _player_original_audio(
    context: StageContext, probe_data: dict[str, Any] | None = None
) -> Path:
    data = probe_data if probe_data is not None else load_json(_probe_file(context))
    suffix = str(_original_audio_delivery_plan(data)["outputSuffix"])
    return context.artifacts_directory / f"original-reference{suffix}"


def _playback_media_report(context: StageContext) -> Path:
    return context.artifacts_directory / "playback-media.json"


def _artifact(path: Path, kind: str, label: str) -> dict[str, Any]:
    return {
        "kind": kind,
        "label": label,
        "path": str(path.resolve()),
        "sizeBytes": path.stat().st_size if path.is_file() else None,
    }


def _run(
    context: StageContext,
    command: list[str],
    *,
    progress: float | None = None,
) -> subprocess.CompletedProcess[str]:
    context.checkpoint()
    actual_command = list(command)
    executable = Path(actual_command[0]).stem.lower()
    stdout_is_media = any(argument in {"-", "pipe:1"} for argument in actual_command[1:])
    machine_progress = (
        executable.startswith("ffmpeg")
        and not executable.startswith("ffprobe")
        and not stdout_is_media
    )
    if machine_progress and "-progress" not in actual_command:
        actual_command[1:1] = ["-progress", "pipe:1", "-nostats"]
    initial_progress = 0.0
    if machine_progress and progress is not None:
        current_job = context.store.load(context.job_id)
        current_stage = next(
            (
                stage
                for stage in current_job.get("stages", [])
                if stage["key"] == context.stage_key
            ),
            None,
        )
        if current_stage:
            initial_progress = float(current_stage.get("progressPercent", 0.0))
    context.log("Executable: " + (Path(actual_command[0]).name or "<executable>"))
    redact_next_argument = False
    for argument in actual_command[1:]:
        lower = argument.lower()
        sensitive_flag = lower in {
            "--token",
            "--password",
            "--secret",
            "--authorization",
            "--credential",
            "--api-key",
        }
        rendered = "<redacted>" if redact_next_argument else argument
        context.log("Argument: " + rendered)
        redact_next_argument = sensitive_flag and not redact_next_argument
    process = subprocess.Popen(
        actual_command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL,
    )
    assert process.stdout is not None
    assert process.stderr is not None
    output_queue: queue.Queue[tuple[str, bytes] | None] = queue.Queue(maxsize=128)

    def emit_output_line(message: str, *, stream: str, level: str = "INFO") -> None:
        output_line = getattr(context, "output_line", None)
        if callable(output_line):
            output_line(message, stream=stream, level=level)
        else:
            context.log(message)

    def read_output(stream: str, pipe: Any) -> None:
        pending = bytearray()
        truncated = False
        read_available = getattr(pipe, "read1", pipe.read)
        while True:
            block = read_available(4096)
            if not block:
                break
            for byte in block:
                if byte in {10, 13}:
                    if pending:
                        if truncated:
                            pending.extend(b" ... <line truncated>")
                        output_queue.put((stream, bytes(pending)))
                    pending.clear()
                    truncated = False
                elif not truncated:
                    if len(pending) < 16 * 1024:
                        pending.append(byte)
                    else:
                        truncated = True
        if pending:
            if truncated:
                pending.extend(b" ... <line truncated>")
            output_queue.put((stream, bytes(pending)))
        output_queue.put(None)

    readers = [
        threading.Thread(target=read_output, args=("stdout", process.stdout), daemon=True),
        threading.Thread(target=read_output, args=("stderr", process.stderr), daemon=True),
    ]
    for reader in readers:
        reader.start()
    captured_stdout: list[str] = []
    captured_stderr: list[str] = []
    captured_bytes = {"stdout": 0, "stderr": 0}
    readers_done = 0
    duration = _command_duration_seconds(actual_command)
    last_machine_progress = 0.0

    def terminate_and_drain() -> None:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        deadline = time.monotonic() + 5.0
        while any(reader.is_alive() for reader in readers) and time.monotonic() < deadline:
            try:
                output_queue.get(timeout=0.05)
            except queue.Empty:
                pass
        for reader in readers:
            reader.join(timeout=0.2)

    while process.poll() is None or readers_done < len(readers):
        try:
            item = output_queue.get(timeout=0.1)
        except queue.Empty:
            item = b""
        try:
            if item is None:
                readers_done += 1
            elif item:
                stream, encoded_line = item
                line = encoded_line.decode("utf-8", errors="replace")
                if captured_bytes[stream] < 1024 * 1024:
                    remaining = 1024 * 1024 - captured_bytes[stream]
                    encoded = line.encode("utf-8")[:remaining]
                    target = captured_stdout if stream == "stdout" else captured_stderr
                    target.append(encoded.decode("utf-8", errors="replace"))
                    captured_bytes[stream] += len(encoded)
                if machine_progress and stream == "stdout" and (
                    line.startswith("out_time_") or line.startswith("progress=")
                ):
                    emit_output_line(line, stream="progress")
                    if progress is not None and duration:
                        elapsed = _ffmpeg_progress_seconds(line)
                        now = time.monotonic()
                        if elapsed is not None and (
                            now - last_machine_progress >= 0.2 or elapsed >= duration
                        ):
                            context.progress(
                                min(
                                    float(progress),
                                    initial_progress
                                    + (float(progress) - initial_progress)
                                    * min(1.0, elapsed / duration),
                                ),
                                f"FFmpeg {min(100.0, elapsed / duration * 100.0):.1f}%",
                            )
                            last_machine_progress = now
                else:
                    emit_output_line(
                        line,
                        stream=stream,
                        level="WARNING" if stream == "stderr" else "INFO",
                    )
            if process.poll() is None:
                context.checkpoint()
        except BaseException:
            terminate_and_drain()
            raise
    for reader in readers:
        reader.join(timeout=5)
    returncode = process.wait()
    output = "\n".join(captured_stdout).strip()
    error_output = "\n".join(captured_stderr).strip()
    completed = subprocess.CompletedProcess(
        actual_command,
        returncode,
        stdout=output,
        stderr=error_output,
    )
    if returncode:
        diagnostics = error_output or output
        tail = "\n".join(diagnostics.splitlines()[-20:])
        raise RuntimeError(
            f"Command failed with exit code {returncode}: {tail}"
        )
    if progress is not None:
        context.progress(progress)
    context.checkpoint()
    return completed


def _time_value_seconds(value: str) -> float | None:
    try:
        parts = [float(part) for part in value.split(":")]
    except ValueError:
        return None
    if len(parts) == 1:
        return parts[0]
    if len(parts) == 2:
        return parts[0] * 60 + parts[1]
    if len(parts) == 3:
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    return None


def _command_duration_seconds(command: list[str]) -> float | None:
    for option in ("-t", "-to"):
        if option in command:
            index = command.index(option)
            if index + 1 < len(command):
                duration = _time_value_seconds(command[index + 1])
                if duration is not None and duration > 0:
                    if option == "-to" and "-ss" in command:
                        start_index = command.index("-ss")
                        if start_index + 1 < len(command):
                            start = _time_value_seconds(command[start_index + 1]) or 0.0
                            duration -= start
                    return duration if duration > 0 else None
    return None


def _ffmpeg_progress_seconds(line: str) -> float | None:
    key, separator, value = line.partition("=")
    if not separator:
        return None
    try:
        if key == "out_time_us":
            return max(0.0, float(value) / 1_000_000.0)
        if key == "out_time_ms":
            # FFmpeg historically labels this field ms while emitting microseconds.
            return max(0.0, float(value) / 1_000_000.0)
        if key == "out_time":
            return _time_value_seconds(value)
    except ValueError:
        return None
    return None


def _ffmpeg(root: Path) -> str:
    return str(resolve_environment_path("LYRICRAIL_FFMPEG", root, "ffmpeg"))


def _ffprobe(root: Path) -> str:
    return str(resolve_environment_path("LYRICRAIL_FFPROBE", root, "ffprobe"))


def _lrail_cli(root: Path) -> str:
    configured = os.environ.get("LYRICRAIL_LRAIL", "").strip()
    candidates = [
        Path(configured).expanduser() if configured else None,
        root / "target" / "release" / ("lrail.exe" if os.name == "nt" else "lrail"),
        root / "target" / "debug" / ("lrail.exe" if os.name == "nt" else "lrail"),
    ]
    for candidate in candidates:
        if candidate and candidate.is_file():
            return str(candidate.resolve())
    discovered = shutil.which("lrail")
    if discovered:
        return discovered
    raise RuntimeError(
        "The native lrail package tool is not installed. Build the Rust workspace "
        "or set LYRICRAIL_LRAIL to the signed application binary."
    )


def _sidecar_candidates(source: Path) -> list[Path]:
    candidates = [source.with_suffix(".lyricrail.json")]
    clean_stem = re.sub(r"\s*\[source\]\s*$", "", source.stem, flags=re.IGNORECASE)
    candidates.append(source.with_name(clean_stem + ".lyricrail.json"))
    return list(dict.fromkeys(candidates))


def load_source_directives(source: Path) -> dict[str, Any]:
    for candidate in _sidecar_candidates(source):
        if candidate.is_file():
            return load_json(candidate)
    return {}


def _probe_file(context: StageContext) -> Path:
    return _shared_work(context) / "media.json"


def _source_audio(context: StageContext) -> Path:
    return _shared_work(context) / "source.wav"


def _instrumental(context: StageContext) -> Path:
    return _shared_work(context) / "instrumental.flac"


def _vocals(context: StageContext) -> Path:
    return _shared_work(context) / "vocals.flac"


def _stem_qc_file(context: StageContext) -> Path:
    return _shared_work(context) / "separation-qc.json"


def _lyric_leakage_qc_file(context: StageContext) -> Path:
    return _shared_work(context) / "lyric-leakage-qc.json"


def _authoritative_lyrics_file(context: StageContext) -> Path:
    return _shared_work(context) / "authoritative-lyrics.json"


def _role_lead(context: StageContext) -> Path:
    return _shared_work(context) / "role-lead.flac"


def _role_accompaniment(context: StageContext) -> Path:
    return _shared_work(context) / "role-accompaniment.flac"


def _role_backing_vocals(context: StageContext) -> Path:
    return _shared_work(context) / "role-backing-vocals.flac"


def _role_gender_male(context: StageContext) -> Path:
    return _shared_work(context) / "role-gender-male.flac"


def _role_gender_female(context: StageContext) -> Path:
    return _shared_work(context) / "role-gender-female.flac"


def _role_analysis_file(context: StageContext) -> Path:
    return _shared_work(context) / "role-analysis.json"


def stem_separation_qc(
    source_path: Path,
    instrumental_path: Path,
    vocal_path: Path,
    settings: dict[str, Any],
) -> dict[str, Any]:
    """Measure structural integrity of a two-stem production separation."""
    import numpy as np
    import soundfile as sf

    paths = {
        "source": source_path,
        "instrumental": instrumental_path,
        "vocals": vocal_path,
    }
    streams = {name: sf.SoundFile(path) for name, path in paths.items()}
    try:
        sample_rates = {name: stream.samplerate for name, stream in streams.items()}
        channels = {name: stream.channels for name, stream in streams.items()}
        frames = {name: len(stream) for name, stream in streams.items()}
        minimum_frames = min(frames.values())
        source_energy = instrumental_energy = vocal_energy = error_energy = 0.0
        sample_count = non_finite = 0
        peaks = {name: 0.0 for name in streams}
        block_size = 262_144
        while sample_count < minimum_frames * channels["source"]:
            remaining_frames = minimum_frames - streams["source"].tell()
            if remaining_frames <= 0:
                break
            count = min(block_size, remaining_frames)
            blocks = {
                name: stream.read(count, dtype="float32", always_2d=True)
                for name, stream in streams.items()
            }
            usable = min(len(block) for block in blocks.values())
            if not usable:
                break
            blocks = {name: block[:usable] for name, block in blocks.items()}
            for name, block in blocks.items():
                non_finite += int(block.size - np.count_nonzero(np.isfinite(block)))
                peaks[name] = max(peaks[name], float(np.nanmax(np.abs(block))))
            source = np.nan_to_num(blocks["source"])
            instrumental = np.nan_to_num(blocks["instrumental"])
            vocals = np.nan_to_num(blocks["vocals"])
            error = source - instrumental - vocals
            source_energy += float(np.sum(source.astype("float64") ** 2))
            instrumental_energy += float(
                np.sum(instrumental.astype("float64") ** 2)
            )
            vocal_energy += float(np.sum(vocals.astype("float64") ** 2))
            error_energy += float(np.sum(error.astype("float64") ** 2))
            sample_count += source.size
    finally:
        for stream in streams.values():
            stream.close()

    def ratio_db(numerator: float, denominator: float) -> float:
        return 10.0 * math.log10(max(numerator, 1e-20) / max(denominator, 1e-20))

    duration_delta = (
        max(frames[name] / sample_rates[name] for name in frames)
        - min(frames[name] / sample_rates[name] for name in frames)
    )
    reconstruction_snr = ratio_db(source_energy, error_energy)
    instrumental_ratio = ratio_db(instrumental_energy, source_energy)
    vocal_ratio = ratio_db(vocal_energy, source_energy)
    errors: list[dict[str, Any]] = []
    if len(set(sample_rates.values())) != 1:
        errors.append({"code": "STEM_SAMPLE_RATE_MISMATCH", "observed": sample_rates})
    if len(set(channels.values())) != 1:
        errors.append({"code": "STEM_CHANNEL_MISMATCH", "observed": channels})
    if duration_delta > float(settings.get("maximumDurationDeltaSeconds", 0.05)):
        errors.append(
            {
                "code": "STEM_DURATION_MISMATCH",
                "observedSeconds": round(duration_delta, 6),
            }
        )
    # A decoded commercial master can legitimately touch digital full scale.
    # Treating a source peak of exactly 1.0 as a separation failure rejects the
    # input before the model's output can be evaluated.  Output stems retain the
    # stricter headroom gate because they are the assets LyricRail controls.
    maximum_peak = float(settings.get("maximumSamplePeak", 0.999))
    maximum_source_peak = float(settings.get("maximumSourceSamplePeak", 1.0))
    for name, peak in peaks.items():
        permitted_peak = maximum_source_peak if name == "source" else maximum_peak
        if peak > permitted_peak:
            errors.append(
                {
                    "code": "STEM_SAMPLE_PEAK_HIGH",
                    "stem": name,
                    "observed": peak,
                    "maximum": permitted_peak,
                }
            )
    if non_finite:
        errors.append({"code": "STEM_NON_FINITE_SAMPLES", "observed": non_finite})
    bounds = (
        (
            "STEM_RECONSTRUCTION_SNR_LOW",
            reconstruction_snr,
            float(settings.get("minimumReconstructionSnrDb", 15.0)),
            None,
        ),
        (
            "INSTRUMENT_ENERGY_OUT_OF_RANGE",
            instrumental_ratio,
            float(settings.get("minimumInstrumentEnergyRatioDb", -18.0)),
            float(settings.get("maximumInstrumentEnergyRatioDb", 3.0)),
        ),
        (
            "VOCAL_ENERGY_OUT_OF_RANGE",
            vocal_ratio,
            float(settings.get("minimumVocalEnergyRatioDb", -24.0)),
            float(settings.get("maximumVocalEnergyRatioDb", 3.0)),
        ),
    )
    for code, value, minimum, maximum in bounds:
        if value < minimum or (maximum is not None and value > maximum):
            errors.append(
                {
                    "code": code,
                    "observedDb": round(value, 4),
                    "minimumDb": minimum,
                    "maximumDb": maximum,
                }
            )
    return {
        "status": "failed" if errors else "passed",
        "errors": errors,
        "metrics": {
            "sampleRates": sample_rates,
            "channels": channels,
            "frames": frames,
            "durationDeltaSeconds": round(duration_delta, 6),
            "samplePeaks": {name: round(value, 6) for name, value in peaks.items()},
            "reconstructionSnrDb": round(reconstruction_snr, 4),
            "instrumentEnergyRatioDb": round(instrumental_ratio, 4),
            "vocalEnergyRatioDb": round(vocal_ratio, 4),
            "nonFiniteSampleCount": non_finite,
        },
    }


def _guarded_lexical_cleanup_candidate(
    instrumental: Any,
    vocals: Any,
    sample_rate: int,
    intervals: list[tuple[float, float]],
    settings: dict[str, Any],
    *,
    strength: float,
) -> tuple[Any, dict[str, Any]]:
    """Build a reversible candidate with a bounded local vocal projection.

    A single least-squares coefficient per word and channel is intentionally
    less expressive than a spectral mask: it can remove only the waveform
    component that is truly shared with the isolated-vocal stem.  That makes
    it unable to reshape unrelated instruments.  Crossfades make every sample
    outside confirmed word windows exactly equal to the natural master.
    """
    import numpy as np

    if instrumental.shape != vocals.shape:
        raise ValueError("Guarded lexical cleanup requires matching stems")
    if not intervals:
        return instrumental.copy(), {
            "strength": round(float(strength), 4),
            "localMusicPreservationSnrDb": 120.0,
            "minimumCoherentLeakageReductionDb": 120.0,
            "maximumPostCoherentLeakageDb": -240.0,
            "changedSampleCount": 0,
            "outsideMaximumAbsoluteDelta": 0.0,
            "samplePeak": round(float(np.max(np.abs(instrumental))), 6),
        }

    fade = max(0.01, float(settings.get("guardedCleanupFadeSeconds", 0.1)))
    padding = max(0.0, float(settings.get("guardedCleanupPaddingSeconds", 0.03)))
    maximum_coefficient = max(
        0.0, float(settings.get("guardedCleanupMaximumCoefficient", 0.2))
    )
    duration_seconds = len(instrumental) / sample_rate
    normalized_intervals = [
        (
            max(0.0, float(start) - padding),
            min(duration_seconds, float(end) + padding),
        )
        for start, end in intervals
        if float(end) > float(start)
    ]
    sample_times = np.arange(len(instrumental), dtype="float64") / sample_rate
    candidate = instrumental.copy()
    changed = np.zeros(len(instrumental), dtype=bool)
    coherent_reductions: list[float] = []
    post_coherent_leakage_db: list[float] = []
    observed_coefficients: list[float] = []
    projection_windows: list[tuple[float, float, int, float]] = []
    for start, end in normalized_intervals:
        core = (sample_times >= start) & (sample_times <= end)
        if not np.any(core):
            continue
        onset = np.clip((sample_times - (start - fade)) / fade, 0.0, 1.0)
        offset = np.clip(((end + fade) - sample_times) / fade, 0.0, 1.0)
        envelope = np.maximum(
            core.astype("float32"), np.minimum(onset, offset).astype("float32")
        )
        changed |= envelope > 0.0
        for channel in range(instrumental.shape[1]):
            vocal_clip = vocals[core, channel].astype("float64")
            instrument_clip = instrumental[core, channel].astype("float64")
            vocal_energy = float(np.dot(vocal_clip, vocal_clip))
            coefficient = float(np.dot(instrument_clip, vocal_clip)) / max(
                vocal_energy, 1e-20
            )
            coefficient = float(
                np.clip(coefficient, -maximum_coefficient, maximum_coefficient)
            )
            observed_coefficients.append(abs(coefficient))
            projection_windows.append((start, end, channel, abs(coefficient)))
            candidate[:, channel] -= (
                vocals[:, channel]
                * envelope
                * float(strength)
                * coefficient
            )

    # Measure the final candidate after every word-local projection.  This is
    # important when adjacent confirmed words have overlapping crossfades: an
    # intermediate measurement could otherwise understate the audible residue.
    for start, end, channel, pre_coefficient in projection_windows:
        core = (sample_times >= start) & (sample_times <= end)
        vocal_clip = vocals[core, channel].astype("float64")
        vocal_energy = float(np.dot(vocal_clip, vocal_clip))
        post_clip = candidate[core, channel].astype("float64")
        post_coefficient = float(np.dot(post_clip, vocal_clip)) / max(
            vocal_energy, 1e-20
        )
        post_absolute = abs(post_coefficient)
        post_coherent_leakage_db.append(
            20.0 * math.log10(max(post_absolute, 1e-12))
        )
        coherent_reductions.append(
            20.0
            * math.log10(
                max(pre_coefficient, 1e-12) / max(post_absolute, 1e-12)
            )
        )

    difference = instrumental - candidate
    local_change_energy = float(
        np.sum(difference[changed].astype("float64") ** 2)
    )
    local_instrumental_energy = float(
        np.sum(instrumental[changed].astype("float64") ** 2)
    )
    local_snr = 10.0 * math.log10(
        max(local_instrumental_energy, 1e-20) / max(local_change_energy, 1e-20)
    )
    outside_delta = float(
        np.max(np.abs(difference[~changed])) if np.any(~changed) else 0.0
    )
    return candidate, {
        "strength": round(float(strength), 4),
        "localMusicPreservationSnrDb": round(local_snr, 4),
        "minimumCoherentLeakageReductionDb": round(
            min(coherent_reductions, default=0.0), 4
        ),
        "maximumPostCoherentLeakageDb": round(
            max(post_coherent_leakage_db, default=-240.0), 4
        ),
        "maximumProjectionCoefficient": round(
            max(observed_coefficients, default=0.0), 6
        ),
        "changedSampleCount": int(np.count_nonzero(changed)),
        "changedDurationSeconds": round(
            float(np.count_nonzero(changed)) / sample_rate, 4
        ),
        "outsideMaximumAbsoluteDelta": round(outside_delta, 12),
        "samplePeak": round(float(np.max(np.abs(candidate))), 6),
    }


def _select_residual_consensus_words(
    words: list[dict[str, Any]],
    primary_matches: list[dict[str, Any]],
    secondary_evidence: list[dict[str, Any]],
    settings: dict[str, Any],
) -> tuple[list[int], dict[str, Any]]:
    """Require independent acoustic evidence before changing a natural stem."""
    primary_by_index = {
        int(item["wordIndex"]): item
        for item in primary_matches
        if item.get("wordIndex") is not None
    }
    secondary_by_index = {
        int(item["wordIndex"]): item for item in secondary_evidence
    }
    thresholds = {
        "minimumPrimaryConfidence": float(
            settings.get("minimumConsensusPrimaryConfidence", 0.35)
        ),
        "minimumPrimaryConsonantConfidence": float(
            settings.get("minimumConsensusPrimaryConsonantConfidence", 0.5)
        ),
        "minimumPrimaryCoherentLeakageDb": float(
            settings.get("minimumConsensusPrimaryCoherentLeakageDb", -17.0)
        ),
        "minimumSecondaryConfidence": float(
            settings.get("minimumConsensusSecondaryConfidence", 0.35)
        ),
        "minimumSecondaryConsonantConfidence": float(
            settings.get("minimumConsensusSecondaryConsonantConfidence", 0.5)
        ),
        "minimumSecondaryVocalCorrelation": float(
            settings.get("minimumConsensusSecondaryVocalCorrelation", 0.04)
        ),
    }
    accepted: list[int] = []
    decisions: list[dict[str, Any]] = []
    for word_index in sorted(primary_by_index):
        if not 0 <= word_index < len(words):
            continue
        primary = primary_by_index[word_index]
        secondary = secondary_by_index.get(word_index, {})
        checks = {
            "primaryConfidence": float(primary.get("residualConfidence", 0.0))
            >= thresholds["minimumPrimaryConfidence"],
            "primaryConsonantConfidence": float(
                primary.get("residualConsonantConfidence", 0.0)
            )
            >= thresholds["minimumPrimaryConsonantConfidence"],
            "primaryCoherentLeakage": float(
                words[word_index].get("preCoherentLeakageDb", -120.0)
            )
            >= thresholds["minimumPrimaryCoherentLeakageDb"],
            "secondaryConfidence": float(
                secondary.get("confidence", 0.0)
            )
            >= thresholds["minimumSecondaryConfidence"],
            "secondaryConsonantConfidence": float(
                secondary.get("consonantConfidence", 0.0)
            )
            >= thresholds["minimumSecondaryConsonantConfidence"],
            "secondaryVocalCorrelation": float(
                secondary.get("vocalCorrelation", 0.0)
            )
            >= thresholds["minimumSecondaryVocalCorrelation"],
        }
        accepted_word = all(checks.values())
        if accepted_word:
            accepted.append(word_index)
        decisions.append(
            {
                "wordIndex": word_index,
                "text": str(words[word_index].get("text", "")),
                "start": float(words[word_index].get("start", 0.0)),
                "accepted": accepted_word,
                "checks": checks,
                "primary": primary,
                "secondary": secondary,
            }
        )
    return accepted, {
        "policy": "two-model-lexical-acoustic-consensus",
        "thresholds": thresholds,
        "primaryCandidateCount": len(primary_by_index),
        "acceptedWordCount": len(accepted),
        "acceptedWordIndexes": accepted,
        "decisions": decisions,
    }


def _select_residual_consensus_intervals(
    primary_vocals: Any,
    secondary_vocals: Any,
    sample_rate: int,
    intervals: list[dict[str, Any]],
    settings: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Confirm unaligned vocal activity with an independent separator.

    A source separator can place a shared instrument in both stems, which makes
    coherence alone an unsafe cleanup signal.  Unaligned activity is accepted
    only when the independent residual-vocal stem is itself audible and has a
    matching spectral shape.  This keeps quiet separator hallucinations out of
    the production cleanup path.
    """
    import numpy as np

    primary = np.asarray(primary_vocals, dtype=np.float32)
    secondary = np.asarray(secondary_vocals, dtype=np.float32)
    if primary.ndim == 1:
        primary = primary[:, None]
    if secondary.ndim == 1:
        secondary = secondary[:, None]
    if primary.shape != secondary.shape:
        raise ValueError("Residual interval consensus stems must have matching shape")
    minimum_rms_dbfs = float(
        settings.get("minimumResidualConsensusRmsDbfs", -45.0)
    )
    minimum_to_primary_db = float(
        settings.get("minimumResidualConsensusToPrimaryDb", -18.0)
    )
    minimum_spectral_cosine = float(
        settings.get("minimumResidualConsensusSpectralCosine", 0.2)
    )
    accepted: list[dict[str, Any]] = []
    decisions: list[dict[str, Any]] = []
    for index, interval in enumerate(intervals):
        start = max(0, round(float(interval["start"]) * sample_rate))
        end = min(len(primary), round(float(interval["end"]) * sample_rate))
        if end <= start:
            continue
        left = primary[start:end].astype(np.float64)
        right = secondary[start:end].astype(np.float64)
        left_rms = float(np.sqrt(np.mean(left**2) + 1e-20))
        right_rms = float(np.sqrt(np.mean(right**2) + 1e-20))
        right_rms_dbfs = 20.0 * math.log10(max(right_rms, 1e-12))
        right_to_left_db = 20.0 * math.log10(
            max(right_rms, 1e-12) / max(left_rms, 1e-12)
        )
        window = np.hanning(end - start)[:, None]
        left_spectrum = np.mean(
            np.abs(np.fft.rfft(left * window, axis=0)), axis=1
        )
        right_spectrum = np.mean(
            np.abs(np.fft.rfft(right * window, axis=0)), axis=1
        )
        denominator = math.sqrt(
            max(float(np.dot(left_spectrum, left_spectrum)), 1e-20)
            * max(float(np.dot(right_spectrum, right_spectrum)), 1e-20)
        )
        spectral_cosine = float(
            np.dot(left_spectrum, right_spectrum) / denominator
        )
        checks = {
            "secondaryAudible": right_rms_dbfs >= minimum_rms_dbfs,
            "secondaryRelativeLevel": right_to_left_db >= minimum_to_primary_db,
            "spectralAgreement": spectral_cosine >= minimum_spectral_cosine,
        }
        decision = {
            "intervalIndex": index,
            "start": float(interval["start"]),
            "end": float(interval["end"]),
            "accepted": all(checks.values()),
            "secondaryRmsDbfs": round(right_rms_dbfs, 4),
            "secondaryToPrimaryDb": round(right_to_left_db, 4),
            "spectralCosine": round(spectral_cosine, 6),
            "checks": checks,
        }
        decisions.append(decision)
        if decision["accepted"]:
            accepted.append(interval)
    return accepted, {
        "policy": "two-model-audible-spectral-consensus",
        "thresholds": {
            "minimumSecondaryRmsDbfs": minimum_rms_dbfs,
            "minimumSecondaryToPrimaryDb": minimum_to_primary_db,
            "minimumSpectralCosine": minimum_spectral_cosine,
        },
        "candidateIntervalCount": len(intervals),
        "acceptedIntervalCount": len(accepted),
        "decisions": decisions,
    }


def _secondary_residual_vocal_evidence(
    project_root: Path,
    instrumental_path: Path,
    vocal_path: Path,
    lines: list[dict[str, Any]],
    words: list[dict[str, Any]],
    target_word_indexes: list[int],
    settings: dict[str, Any],
    lexical_aligner: VietnameseSongAligner,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Extract an independent residual-vocal stem and measure word evidence."""
    import numpy as np
    import soundfile as sf

    residual_path = instrumental_path.with_name("residual-consensus-vocals.flac")
    model = str(
        settings.get(
            "residualConsensusModelFilename", "melband_roformer_inst_v2.ckpt"
        )
    )
    reused = residual_path.is_file()
    if not reused:
        configured_ffmpeg = Path(_ffmpeg(project_root))
        bundled_ffmpeg = (
            project_root
            / "tools"
            / "ffmpeg"
            / "bin"
            / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
        )
        ffmpeg_executable = (
            configured_ffmpeg
            if configured_ffmpeg.is_file()
            else bundled_ffmpeg
            if bundled_ffmpeg.is_file()
            else None
        )
        if ffmpeg_executable is not None:
            _prepend_process_path(ffmpeg_executable.parent)
        _prepare_cuda_runtime()
        try:
            import torch
            from audio_separator.separator import Separator
        except ImportError as exc:
            raise RuntimeError(
                "Two-model residual consensus requires audio-separator and PyTorch"
            ) from exc
        original_torch_load = torch.load

        def compatible_torch_load(*args: Any, **kwargs: Any) -> Any:
            kwargs.setdefault("weights_only", False)
            return original_torch_load(*args, **kwargs)

        separator: Any | None = None
        torch.load = compatible_torch_load
        try:
            separator = Separator(
                output_dir=str(instrumental_path.parent),
                model_file_dir=str(project_root / "models" / "audio-separator"),
                output_format="FLAC",
                sample_rate=int(settings.get("residualConsensusSampleRate", 48_000)),
                use_autocast=bool(
                    settings.get("residualConsensusUseAutocast", True)
                ),
                output_single_stem="Vocals",
            )
            separator.load_model(model_filename=model)
            separator.separate(
                str(instrumental_path),
                {
                    "Vocals": residual_path.stem,
                    "vocals": residual_path.stem,
                },
            )
        finally:
            torch.load = original_torch_load
            del separator
            gc.collect()
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        if not residual_path.is_file():
            matches = list(instrumental_path.parent.glob(residual_path.stem + ".*"))
            if not matches:
                raise RuntimeError(
                    "Residual consensus separator did not produce a vocal stem"
                )
            shutil.move(str(matches[0]), str(residual_path))

    native_vocals, sample_rate = sf.read(
        vocal_path, dtype="float32", always_2d=True
    )
    native_residual, residual_rate = sf.read(
        residual_path, dtype="float32", always_2d=True
    )
    if sample_rate != residual_rate or native_vocals.shape != native_residual.shape:
        raise ValueError("Residual consensus stems must have matching shape")
    residual_waveform = lexical_aligner.load_audio(residual_path)
    target_set = set(target_word_indexes)
    correlation_padding = float(
        settings.get("residualConsensusCorrelationPaddingSeconds", 0.08)
    )
    correlation_window = float(
        settings.get("residualConsensusCorrelationWindowSeconds", 0.8)
    )
    evidence: list[dict[str, Any]] = []
    word_cursor = 0
    for line in lines:
        raw_words = [str(item["text"]) for item in line.get("syllables", [])]
        if not raw_words:
            continue
        aligned = lexical_aligner.align_window(
            residual_waveform,
            window_start=max(0.0, float(line["start"]) - 0.12),
            window_end=float(line["end"]) + 0.12,
            raw_words=raw_words,
        )
        for offset, aligned_word in enumerate(aligned):
            word_index = word_cursor + offset
            if word_index not in target_set:
                continue
            consonants = [
                float(value)
                for value in aligned_word.get("consonantConfidences", [])
            ]
            start = max(
                0.0, float(words[word_index]["start"]) - correlation_padding
            )
            end = min(
                len(native_vocals) / sample_rate,
                float(words[word_index]["start"]) + correlation_window,
            )
            sample_start = int(start * sample_rate)
            sample_end = max(sample_start + 1, int(end * sample_rate))
            left = np.mean(
                native_vocals[sample_start:sample_end], axis=1
            ).astype("float64")
            right = np.mean(
                native_residual[sample_start:sample_end], axis=1
            ).astype("float64")
            denominator = math.sqrt(
                max(float(np.dot(left, left)), 1e-20)
                * max(float(np.dot(right, right)), 1e-20)
            )
            correlation = abs(float(np.dot(left, right)) / denominator)
            evidence.append(
                {
                    "wordIndex": word_index,
                    "text": str(words[word_index]["text"]),
                    "confidence": round(float(aligned_word["confidence"]), 4),
                    "consonantConfidence": round(
                        sum(consonants) / len(consonants) if consonants else 0.0,
                        4,
                    ),
                    "vocalCorrelation": round(correlation, 6),
                }
            )
        word_cursor += len(raw_words)
    return evidence, {
        "model": model,
        "residualStem": str(residual_path.resolve()),
        "reused": reused,
        "evidenceCount": len(evidence),
    }


def refine_lyric_leakage(
    instrumental_path: Path,
    vocal_path: Path,
    lines: list[dict[str, Any]],
    settings: dict[str, Any],
    *,
    project_root: Path | None = None,
    alignment_settings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Suppress both aligned lyrics and unaligned vocal fragments in the music stem.

    Lyric windows alone are insufficient for karaoke masters: an ad-lib, laugh,
    call, or harmony may have no corresponding subtitle token.  The residual
    activity scan therefore finds vocal-correlated time-frequency energy across
    the entire isolated-vocal stem.  Every accepted change is transferred to
    the vocal stem so the two stems continue to reconstruct the source exactly.
    """
    import librosa
    import numpy as np
    import soundfile as sf

    if not bool(settings.get("enabled", True)):
        return {"status": "disabled", "errors": [], "metrics": {}}
    residual_policy = str(
        settings.get("residualVocalPolicy", "remove-fragments")
    ).strip()
    if residual_policy != "remove-fragments":
        raise ValueError(
            "Unsupported residual-vocal policy: " + residual_policy
        )
    instrumental, sample_rate = sf.read(
        instrumental_path, dtype="float32", always_2d=True
    )
    vocals, vocal_sample_rate = sf.read(vocal_path, dtype="float32", always_2d=True)
    if sample_rate != vocal_sample_rate or instrumental.shape != vocals.shape:
        raise ValueError("Lyric-leakage refinement requires matching lossless stems")
    analysis_rate = int(settings.get("analysisSampleRate", 16_000))

    def analysis_audio(values: Any) -> Any:
        mono = np.mean(values, axis=1, dtype="float32")
        if sample_rate == analysis_rate:
            return mono
        return librosa.resample(
            mono, orig_sr=sample_rate, target_sr=analysis_rate
        )

    vocal_analysis = analysis_audio(vocals)
    instrumental_analysis = analysis_audio(instrumental)
    analysis_fft = int(settings.get("analysisFftSize", 512))
    analysis_hop = int(settings.get("analysisHopSize", 128))
    minimum_hz = float(settings.get("minimumVocalFrequencyHz", 120.0))
    maximum_hz = float(settings.get("maximumVocalFrequencyHz", 6_000.0))
    frequencies = librosa.fft_frequencies(sr=analysis_rate, n_fft=analysis_fft)
    frequency_band = (frequencies >= minimum_hz) & (frequencies <= maximum_hz)
    padding = float(settings.get("wordPaddingSeconds", 0.05))

    def word_overlap_db(values: Any, start: float, end: float) -> float:
        sample_start = max(0, round((start - padding) * analysis_rate))
        sample_end = min(len(values), round((end + padding) * analysis_rate))
        vocal_clip = vocal_analysis[sample_start:sample_end]
        instrumental_clip = values[sample_start:sample_end]
        if min(len(vocal_clip), len(instrumental_clip)) < analysis_fft:
            return -120.0
        vocal_spectrum = np.abs(
            librosa.stft(
                vocal_clip,
                n_fft=analysis_fft,
                hop_length=analysis_hop,
                window="hann",
            )
        )[frequency_band]
        instrumental_spectrum = np.abs(
            librosa.stft(
                instrumental_clip,
                n_fft=analysis_fft,
                hop_length=analysis_hop,
                window="hann",
            )
        )[frequency_band]
        shared_energy = float(
            np.sum(np.minimum(vocal_spectrum, instrumental_spectrum) ** 2)
        )
        vocal_energy = float(np.sum(vocal_spectrum**2))
        return 10.0 * math.log10(
            max(shared_energy, 1e-20) / max(vocal_energy, 1e-20)
        )

    def coherent_leakage_db(values: Any, start: float, end: float) -> float:
        """Measure only the component phase-coherent with the vocal stem."""
        sample_start = max(0, round((start - padding) * analysis_rate))
        sample_end = min(len(values), round((end + padding) * analysis_rate))
        vocal_clip = vocal_analysis[sample_start:sample_end]
        instrumental_clip = values[sample_start:sample_end]
        if min(len(vocal_clip), len(instrumental_clip)) < analysis_fft:
            return -120.0
        vocal_spectrum = librosa.stft(
            vocal_clip,
            n_fft=analysis_fft,
            hop_length=analysis_hop,
            window="hann",
        )[frequency_band]
        instrumental_spectrum = librosa.stft(
            instrumental_clip,
            n_fft=analysis_fft,
            hop_length=analysis_hop,
            window="hann",
        )[frequency_band]
        vocal_energy_by_bin = np.sum(np.abs(vocal_spectrum) ** 2, axis=1)
        cross = np.sum(
            instrumental_spectrum * np.conj(vocal_spectrum), axis=1
        )
        coefficient = cross / (vocal_energy_by_bin + 1e-12)
        coefficient_magnitude = np.abs(coefficient)
        coefficient *= np.minimum(
            1.0, 1.0 / (coefficient_magnitude + 1e-12)
        )
        coherent_energy = float(
            np.sum(
                np.abs(coefficient[:, None] * vocal_spectrum) ** 2
            )
        )
        vocal_energy = float(np.sum(np.abs(vocal_spectrum) ** 2))
        return 10.0 * math.log10(
            max(coherent_energy, 1e-20) / max(vocal_energy, 1e-20)
        )

    review_threshold = float(settings.get("reviewSpectralOverlapDb", -6.0))
    words: list[dict[str, Any]] = []
    for line in lines:
        for word in line.get("syllables", []):
            start, end = float(word["start"]), float(word["end"])
            score = word_overlap_db(instrumental_analysis, start, end)
            words.append(
                {
                    "text": str(word["text"]),
                    "start": round(start, 3),
                    "end": round(end, 3),
                    "acousticEnd": round(
                        min(end, float(word.get("acousticEnd", end))), 3
                    ),
                    "preSpectralOverlapDb": round(score, 4),
                    "preCoherentLeakageDb": round(
                        coherent_leakage_db(instrumental_analysis, start, end),
                        4,
                    ),
                }
            )
    flagged = [
        item for item in words if item["preSpectralOverlapDb"] >= review_threshold
    ]

    lexical_enabled = bool(settings.get("lexicalResidualAuditEnabled", True))
    lexical_aligner: VietnameseSongAligner | None = None
    lexical_vocal_waveform: Any | None = None

    def lexical_residual_audit(
        residual_waveform: Any,
        *,
        coherent_metric: str,
    ) -> tuple[dict[str, Any], list[int]]:
        """Require both CTC phonemes and phase-coherent vocal evidence."""
        if not lexical_enabled or project_root is None:
            return (
                {
                    "status": "disabled",
                    "errors": [],
                    "matchedResidualWordCount": 0,
                    "matchedResidualWords": [],
                },
                [],
            )
        assert lexical_aligner is not None
        assert lexical_vocal_waveform is not None
        duration_seconds = residual_waveform.shape[1] / 16000
        window_padding = float(
            settings.get("lexicalResidualWindowPaddingSeconds", 0.12)
        )
        minimum_reference = float(
            settings.get("minimumLexicalReferenceConfidence", 0.45)
        )
        minimum_residual = float(
            settings.get("minimumLexicalResidualConfidence", 0.28)
        )
        minimum_ratio = float(
            settings.get("minimumLexicalResidualConfidenceRatio", 0.3)
        )
        minimum_consonant = float(
            settings.get("minimumLexicalResidualConsonantConfidence", 0.3)
        )
        minimum_consonant_ratio = float(
            settings.get(
                "minimumLexicalResidualConsonantConfidenceRatio", 0.35
            )
        )
        maximum_onset_delta = float(
            settings.get("maximumLexicalResidualOnsetDeltaSeconds", 0.2)
        )
        maximum_lexical_coherent_leakage = float(
            settings.get("maximumLexicalCoherentLeakageDb", -24.0)
        )
        matched_residual_words: list[dict[str, Any]] = []
        matched_word_indexes: list[int] = []
        word_audit_cursor = 0
        for line_index, line in enumerate(lines, start=1):
            raw_words = [
                str(item["text"]) for item in line.get("syllables", [])
            ]
            if not raw_words:
                continue
            window_start = max(0.0, float(line["start"]) - window_padding)
            window_end = min(
                duration_seconds, float(line["end"]) + window_padding
            )
            reference = lexical_aligner.align_window(
                lexical_vocal_waveform,
                window_start=window_start,
                window_end=window_end,
                raw_words=raw_words,
            )
            residual = lexical_aligner.align_window(
                residual_waveform,
                window_start=window_start,
                window_end=window_end,
                raw_words=raw_words,
            )
            audited_words = words[
                word_audit_cursor : word_audit_cursor + len(raw_words)
            ]
            first_word_index = word_audit_cursor
            word_audit_cursor += len(raw_words)
            for offset, (left, right, audited) in enumerate(
                zip(reference, residual, audited_words)
            ):
                left_consonants = [
                    float(value)
                    for value in left.get("consonantConfidences", [])
                ]
                right_consonants = [
                    float(value)
                    for value in right.get("consonantConfidences", [])
                ]
                if not left_consonants or not right_consonants:
                    continue
                left_consonant = sum(left_consonants) / len(left_consonants)
                right_consonant = sum(right_consonants) / len(right_consonants)
                left_confidence = float(left["confidence"])
                right_confidence = float(right["confidence"])
                onset_delta = abs(float(left["start"]) - float(right["start"]))
                coherent_leakage = float(audited[coherent_metric])
                matched = (
                    left_confidence >= minimum_reference
                    and right_confidence >= minimum_residual
                    and right_confidence
                    >= minimum_ratio * max(left_confidence, 1e-9)
                    and right_consonant >= minimum_consonant
                    and right_consonant
                    >= minimum_consonant_ratio * max(left_consonant, 1e-9)
                    and onset_delta <= maximum_onset_delta
                    and coherent_leakage > maximum_lexical_coherent_leakage
                )
                if matched:
                    matched_word_indexes.append(first_word_index + offset)
                    matched_residual_words.append(
                        {
                            "wordIndex": first_word_index + offset,
                            "line": line_index,
                            "text": str(right["text"]),
                            "referenceStart": round(float(left["start"]), 4),
                            "residualStart": round(float(right["start"]), 4),
                            "onsetDeltaSeconds": round(onset_delta, 4),
                            "referenceConfidence": round(left_confidence, 4),
                            "residualConfidence": round(right_confidence, 4),
                            "referenceConsonantConfidence": round(
                                left_consonant, 4
                            ),
                            "residualConsonantConfidence": round(
                                right_consonant, 4
                            ),
                            "coherentLeakageDb": round(coherent_leakage, 4),
                        }
                    )
        return (
            {
                "status": "failed" if matched_residual_words else "passed",
                "errors": (
                    [
                        {
                            "code": "LEXICAL_VOCAL_RESIDUAL_DETECTED",
                            "observedWords": len(matched_residual_words),
                        }
                    ]
                    if matched_residual_words
                    else []
                ),
                "matchedResidualWordCount": len(matched_residual_words),
                "matchedResidualWords": matched_residual_words,
            },
            matched_word_indexes,
        )

    pre_lexical_audit: dict[str, Any] = {
        "status": "disabled",
        "errors": [],
        "matchedResidualWordCount": 0,
        "matchedResidualWords": [],
    }
    lexical_target_word_indexes: list[int] = []
    if lexical_enabled and project_root is not None:
        try:
            lexical_aligner = get_vietnamese_song_aligner(
                project_root, alignment_settings or {}
            )
            lexical_vocal_waveform = lexical_aligner.load_audio(vocal_path)
            pre_lexical_audit, lexical_target_word_indexes = (
                lexical_residual_audit(
                    lexical_aligner.load_audio(instrumental_path),
                    coherent_metric="preCoherentLeakageDb",
                )
            )
        except Exception as exc:
            pre_lexical_audit = {
                "status": "failed",
                "errors": [
                    {
                        "code": "LEXICAL_RESIDUAL_AUDIT_FAILED",
                        "message": str(exc),
                    }
                ],
                "matchedResidualWordCount": 0,
                "matchedResidualWords": [],
            }

    def merge_intervals(
        values: list[tuple[float, float]], *, maximum_gap: float
    ) -> list[tuple[float, float]]:
        merged: list[list[float]] = []
        for start, end in sorted(values):
            if end <= start:
                continue
            if merged and start <= merged[-1][1] + maximum_gap:
                merged[-1][1] = max(merged[-1][1], end)
            else:
                merged.append([start, end])
        return [(start, end) for start, end in merged]

    lyric_intervals = [
        (float(item["start"]), float(item["end"])) for item in words
    ]
    residual_intervals: list[dict[str, Any]] = []
    scan_enabled = bool(settings.get("scanUnalignedVocalActivity", True))
    scan_threshold = float(
        settings.get("residualActivityReviewSpectralOverlapDb", -6.0)
    )
    if scan_enabled:
        vocal_spectrum = np.abs(
            librosa.stft(
                vocal_analysis,
                n_fft=analysis_fft,
                hop_length=analysis_hop,
                window="hann",
            )
        )[frequency_band]
        instrumental_spectrum = np.abs(
            librosa.stft(
                instrumental_analysis,
                n_fft=analysis_fft,
                hop_length=analysis_hop,
                window="hann",
            )
        )[frequency_band]
        vocal_frame_energy = np.sum(vocal_spectrum**2, axis=0)
        shared_frame_energy = np.sum(
            np.minimum(vocal_spectrum, instrumental_spectrum) ** 2, axis=0
        )
        overlap_db = 10.0 * np.log10(
            np.maximum(shared_frame_energy, 1e-20)
            / np.maximum(vocal_frame_energy, 1e-20)
        )
        maximum_vocal_energy = max(float(np.max(vocal_frame_energy)), 1e-20)
        activity_db = 10.0 * np.log10(
            np.maximum(vocal_frame_energy, 1e-20) / maximum_vocal_energy
        )
        minimum_activity_db = float(
            settings.get("minimumResidualVocalActivityDb", -30.0)
        )
        frame_times = librosa.frames_to_time(
            np.arange(vocal_frame_energy.shape[0]),
            sr=analysis_rate,
            hop_length=analysis_hop,
        )
        lyric_guard = float(
            settings.get("residualActivityLyricGuardSeconds", 0.08)
        )
        aligned_frames = np.zeros(vocal_frame_energy.shape[0], dtype=bool)
        for lyric_start, lyric_end in lyric_intervals:
            aligned_frames |= (
                (frame_times >= lyric_start - lyric_guard)
                & (frame_times <= lyric_end + lyric_guard)
            )
        candidate_frames = np.flatnonzero(
            (activity_db >= minimum_activity_db) & (overlap_db >= scan_threshold)
            & ~aligned_frames
        )
        frame_seconds = analysis_hop / analysis_rate
        merge_gap = float(settings.get("residualActivityMergeGapSeconds", 0.12))
        minimum_duration = float(
            settings.get("minimumResidualActivityDurationSeconds", 0.25)
        )
        scan_padding = float(settings.get("residualActivityPaddingSeconds", 0.06))
        raw_intervals: list[tuple[float, float]] = []
        if candidate_frames.size:
            run_start = previous = int(candidate_frames[0])
            maximum_gap_frames = max(1, int(round(merge_gap / frame_seconds)))
            for frame in map(int, candidate_frames[1:]):
                if frame - previous > maximum_gap_frames:
                    raw_intervals.append(
                        (
                            max(0.0, run_start * frame_seconds - scan_padding),
                            min(
                                len(vocal_analysis) / analysis_rate,
                                (previous + 1) * frame_seconds + scan_padding,
                            ),
                        )
                    )
                    run_start = frame
                previous = frame
            raw_intervals.append(
                (
                    max(0.0, run_start * frame_seconds - scan_padding),
                    min(
                        len(vocal_analysis) / analysis_rate,
                        (previous + 1) * frame_seconds + scan_padding,
                    ),
                )
            )
        for start, end in merge_intervals(raw_intervals, maximum_gap=merge_gap):
            if end - start < minimum_duration:
                continue
            maximum_residual_duration = float(
                settings.get("maximumResidualActivityDurationSeconds", 4.0)
            )
            lyric_edge_context = float(
                settings.get("residualActivityLyricEdgeContextSeconds", 0.75)
            )
            if end - start > maximum_residual_duration:
                upcoming = [
                    lyric_start
                    for lyric_start, _ in lyric_intervals
                    if start < lyric_start <= end + lyric_guard + scan_padding
                ]
                preceding = [
                    lyric_end
                    for _, lyric_end in lyric_intervals
                    if start - lyric_guard - scan_padding <= lyric_end < end
                ]
                if upcoming:
                    lyric_start = min(upcoming)
                    end = min(end, lyric_start)
                    start = max(start, end - lyric_edge_context)
                elif preceding:
                    lyric_end = max(preceding)
                    start = max(start, lyric_end)
                    end = min(end, start + lyric_edge_context)
            coherent_score = coherent_leakage_db(
                instrumental_analysis, start, end
            )
            minimum_coherent_score = float(
                settings.get("residualActivityReviewCoherentLeakageDb", -30.0)
            )
            if coherent_score < minimum_coherent_score:
                continue
            boundary_tolerance = lyric_guard + scan_padding
            lyric_boundary = None
            if any(
                abs(end - lyric_start) <= boundary_tolerance
                for lyric_start, _ in lyric_intervals
            ):
                lyric_boundary = "onset"
            elif any(
                abs(start - lyric_end) <= boundary_tolerance
                for _, lyric_end in lyric_intervals
            ):
                lyric_boundary = "end"
            residual_intervals.append(
                {
                    "start": round(start, 3),
                    "end": round(end, 3),
                    "preSpectralOverlapDb": round(
                        word_overlap_db(instrumental_analysis, start, end), 4
                    ),
                    "preCoherentLeakageDb": round(coherent_score, 4),
                    "alignedWordCoverageRatio": 0.0,
                    "unaligned": True,
                    "lyricBoundary": lyric_boundary,
                }
            )

    if not words and not residual_intervals:
        return {
            "status": "passed",
            "policy": residual_policy,
            "errors": [],
            "metrics": {
                "wordCount": len(words),
                "flaggedWordCount": 0,
                "residualActivityIntervalCount": 0,
                "unalignedResidualActivityIntervalCount": 0,
                "reviewSpectralOverlapDb": review_threshold,
                "residualActivityReviewSpectralOverlapDb": scan_threshold,
                "refinementApplied": False,
            },
            "flaggedWords": [],
            "residualActivityIntervals": [],
        }

    refinement_mode = str(
        settings.get("refinementMode", "bounded")
    ).strip()
    if refinement_mode == "audit-only":
        warnings: list[dict[str, Any]] = []
        if flagged:
            warnings.append(
                {
                    "code": "ALIGNED_VOCAL_OVERLAP_OBSERVED",
                    "observedWords": len(flagged),
                }
            )
        lexical_count = int(
            pre_lexical_audit.get("matchedResidualWordCount", 0)
        )
        if lexical_count:
            warnings.append(
                {
                    "code": "LEXICAL_VOCAL_RESIDUAL_OBSERVED",
                    "observedWords": lexical_count,
                }
            )
        if residual_intervals:
            warnings.append(
                {
                    "code": "UNALIGNED_VOCAL_ACTIVITY_OBSERVED",
                    "observedIntervals": len(residual_intervals),
                }
            )
        return {
            "status": "passed-with-warnings" if warnings else "passed",
            "policy": "audit-only-natural-preservation",
            "errors": [],
            "warnings": warnings,
            "metrics": {
                "wordCount": len(words),
                "flaggedWordCount": len(flagged),
                "residualActivityIntervalCount": len(residual_intervals),
                "unalignedResidualActivityIntervalCount": sum(
                    bool(item["unaligned"]) for item in residual_intervals
                ),
                "reviewSpectralOverlapDb": review_threshold,
                "residualActivityReviewSpectralOverlapDb": scan_threshold,
                "targetedWordRefinementCount": 0,
                "refinementApplied": False,
                "naturalStemPreserved": True,
            },
            "flaggedWords": flagged,
            "wordAudit": words,
            "preLexicalResidualAudit": pre_lexical_audit,
            "lexicalResidualAudit": pre_lexical_audit,
            "terminalResidualActions": [],
            "residualActivityIntervals": residual_intervals,
        }
    if refinement_mode == "lexical-guarded":
        warnings: list[dict[str, Any]] = []
        primary_target_indexes = sorted(set(lexical_target_word_indexes))
        target_word_indexes: list[int] = []
        confirmed_residual_intervals: list[dict[str, Any]] = []
        consensus_report: dict[str, Any] = {
            "policy": "two-model-lexical-acoustic-consensus",
            "status": "not-run",
            "acceptedWordCount": 0,
            "acceptedWordIndexes": [],
        }
        interval_consensus_report: dict[str, Any] = {
            "policy": "two-model-audible-spectral-consensus",
            "status": "not-run",
            "candidateIntervalCount": len(residual_intervals),
            "acceptedIntervalCount": 0,
            "decisions": [],
        }
        lexical_audit_errors = [
            item
            for item in pre_lexical_audit.get("errors", [])
            if item.get("code") != "LEXICAL_VOCAL_RESIDUAL_DETECTED"
        ]
        skip_reason: str | None = None
        if lexical_audit_errors:
            skip_reason = "lexical-audit-unavailable"
            warnings.extend(lexical_audit_errors)
        if not primary_target_indexes and skip_reason is None:
            skip_reason = "no-lexically-confirmed-residual"
        secondary_needed = bool(primary_target_indexes or residual_intervals)
        if secondary_needed and (project_root is None or lexical_aligner is None):
            if primary_target_indexes:
                skip_reason = "secondary-consensus-unavailable"
            warnings.append(
                {
                    "code": "RESIDUAL_CONSENSUS_UNAVAILABLE",
                    "observedWords": len(primary_target_indexes),
                    "observedIntervals": len(residual_intervals),
                }
            )
        elif secondary_needed:
            try:
                secondary_evidence, secondary_report = (
                    _secondary_residual_vocal_evidence(
                        project_root,
                        instrumental_path,
                        vocal_path,
                        lines,
                        words,
                        primary_target_indexes,
                        settings,
                        lexical_aligner,
                    )
                )
                if not lexical_audit_errors and primary_target_indexes:
                    target_word_indexes, consensus_report = (
                        _select_residual_consensus_words(
                            words,
                            pre_lexical_audit.get("matchedResidualWords", []),
                            secondary_evidence,
                            settings,
                        )
                    )
                else:
                    consensus_report = {
                        **consensus_report,
                        "acceptedWordCount": 0,
                        "acceptedWordIndexes": [],
                    }
                consensus_report["status"] = "completed"
                consensus_report["secondaryAnalysis"] = secondary_report
                if residual_intervals:
                    secondary_vocals, secondary_rate = sf.read(
                        Path(secondary_report["residualStem"]),
                        dtype="float32",
                        always_2d=True,
                    )
                    if secondary_rate != sample_rate:
                        raise ValueError(
                            "Residual interval consensus sample rates must match"
                        )
                    (
                        confirmed_residual_intervals,
                        interval_consensus_report,
                    ) = _select_residual_consensus_intervals(
                        vocals,
                        secondary_vocals,
                        sample_rate,
                        residual_intervals,
                        settings,
                    )
                    interval_consensus_report["status"] = "completed"
            except Exception as exc:
                if primary_target_indexes:
                    skip_reason = "secondary-consensus-failed"
                consensus_report = {
                    "policy": "two-model-lexical-acoustic-consensus",
                    "status": "failed",
                    "error": str(exc),
                    "acceptedWordCount": 0,
                    "acceptedWordIndexes": [],
                }
                interval_consensus_report = {
                    **interval_consensus_report,
                    "status": "failed",
                    "error": str(exc),
                }
                warnings.append(
                    {
                        "code": "RESIDUAL_CONSENSUS_ANALYSIS_FAILED",
                        "message": str(exc),
                    }
                )

        target_ratio = len(target_word_indexes) / max(len(words), 1)
        maximum_target_ratio = float(
            settings.get("maximumGuardedCleanupWordRatio", 0.08)
        )
        if primary_target_indexes and skip_reason is None and not target_word_indexes:
            skip_reason = "no-two-model-consensus-residual"
        elif skip_reason is None and target_ratio > maximum_target_ratio:
            skip_reason = "target-scope-too-broad"
            warnings.append(
                {
                    "code": "GUARDED_CLEANUP_TARGET_SCOPE_TOO_BROAD",
                    "observedRatio": round(target_ratio, 4),
                    "maximumRatio": maximum_target_ratio,
                }
            )

        configured_strengths = settings.get(
            "guardedCleanupStrengths", [0.25, 0.5, 0.75, 0.9, 0.95, 1.0]
        )
        if not isinstance(configured_strengths, list):
            raise ValueError("guardedCleanupStrengths must be an array")
        strengths = sorted(
            {
                float(value)
                for value in configured_strengths
                if float(value) > 0.0
            }
        )
        if not strengths:
            raise ValueError("guardedCleanupStrengths must contain a positive value")
        minimum_local_snr = float(
            settings.get("minimumGuardedLocalMusicPreservationSnrDb", 21.0)
        )
        minimum_coherent_reduction = float(
            settings.get("minimumGuardedCoherentReductionDb", 18.0)
        )
        maximum_post_coherent_leakage = float(
            settings.get("maximumGuardedPostCoherentLeakageDb", -42.0)
        )
        maximum_peak = float(settings.get("maximumSamplePeak", 0.999))

        def guarded_attempt(
            base_instrumental: Any,
            base_vocals: Any,
            target_intervals: list[tuple[float, float]],
            candidate_strength: float,
        ) -> tuple[Any, Any, dict[str, Any], bool]:
            candidate, candidate_metrics = _guarded_lexical_cleanup_candidate(
                base_instrumental,
                base_vocals,
                sample_rate,
                target_intervals,
                settings,
                strength=candidate_strength,
            )
            candidate_difference = base_instrumental - candidate
            candidate_vocals = base_vocals + candidate_difference
            candidate_metrics["vocalSamplePeak"] = round(
                float(np.max(np.abs(candidate_vocals))), 6
            )
            preservation_passed = (
                float(candidate_metrics["localMusicPreservationSnrDb"])
                >= minimum_local_snr
                and float(candidate_metrics["samplePeak"]) <= maximum_peak
                and float(candidate_metrics["vocalSamplePeak"]) <= maximum_peak
                and float(candidate_metrics["outsideMaximumAbsoluteDelta"]) == 0.0
            )
            reduction_passed = (
                float(candidate_metrics["minimumCoherentLeakageReductionDb"])
                >= minimum_coherent_reduction
            )
            residual_level_passed = (
                float(candidate_metrics["maximumPostCoherentLeakageDb"])
                <= maximum_post_coherent_leakage
            )
            attempt = {
                **candidate_metrics,
                "preservationGatePassed": preservation_passed,
                "coherentReductionGatePassed": reduction_passed,
                "residualLevelGatePassed": residual_level_passed,
            }
            return (
                candidate,
                candidate_vocals,
                attempt,
                preservation_passed and reduction_passed and residual_level_passed,
            )

        attempts: list[dict[str, Any]] = []
        accepted_candidate: Any | None = None
        accepted_vocals: Any | None = None
        accepted_metrics: dict[str, Any] | None = None
        if skip_reason is None:
            target_intervals = [
                (
                    float(words[index]["start"]),
                    float(
                        max(
                            words[index]["end"],
                            words[index].get("acousticEnd", words[index]["end"]),
                        )
                    ),
                )
                for index in target_word_indexes
            ]
            for candidate_strength in strengths:
                candidate, candidate_vocals, attempt, accepted = guarded_attempt(
                    instrumental,
                    vocals,
                    target_intervals,
                    candidate_strength,
                )
                attempts.append(attempt)
                if accepted:
                    accepted_candidate = candidate
                    accepted_vocals = candidate_vocals
                    accepted_metrics = attempt
                    break

        working_instrumental = (
            accepted_candidate if accepted_candidate is not None else instrumental
        )
        working_vocals = accepted_vocals if accepted_vocals is not None else vocals
        interval_cleanup_attempts: list[dict[str, Any]] = []
        cleaned_residual_intervals: list[dict[str, Any]] = []
        for interval in confirmed_residual_intervals:
            interval_attempt: dict[str, Any] = {
                "start": float(interval["start"]),
                "end": float(interval["end"]),
                "attempts": [],
                "accepted": False,
            }
            target = [(float(interval["start"]), float(interval["end"]))]
            for candidate_strength in strengths:
                candidate, candidate_vocals, attempt, accepted = guarded_attempt(
                    working_instrumental,
                    working_vocals,
                    target,
                    candidate_strength,
                )
                interval_attempt["attempts"].append(attempt)
                if accepted:
                    working_instrumental = candidate
                    working_vocals = candidate_vocals
                    interval_attempt["accepted"] = True
                    interval_attempt["acceptedCandidate"] = attempt
                    cleaned_residual_intervals.append(interval)
                    break
            interval_cleanup_attempts.append(interval_attempt)

        unresolved_confirmed_intervals = (
            len(confirmed_residual_intervals) - len(cleaned_residual_intervals)
        )
        if unresolved_confirmed_intervals:
            warnings.append(
                {
                    "code": "CONFIRMED_UNALIGNED_VOCAL_CLEANUP_REJECTED",
                    "observedIntervals": unresolved_confirmed_intervals,
                    "reason": "no-candidate-passed-all-gates",
                }
            )

        refinement_applied = bool(
            accepted_candidate is not None or cleaned_residual_intervals
        )
        if refinement_applied:
            instrumental_partial = instrumental_path.with_suffix(
                ".guarded.partial.flac"
            )
            vocal_partial = vocal_path.with_suffix(".guarded.partial.flac")
            sf.write(
                instrumental_partial,
                working_instrumental,
                sample_rate,
                format="FLAC",
                subtype="PCM_24",
            )
            sf.write(
                vocal_partial,
                working_vocals,
                sample_rate,
                format="FLAC",
                subtype="PCM_24",
            )
            instrumental_partial.replace(instrumental_path)
            vocal_partial.replace(vocal_path)
        elif target_word_indexes:
            warnings.append(
                {
                    "code": "GUARDED_CLEANUP_REJECTED_NATURAL_STEM_PRESERVED",
                    "targetedWords": len(target_word_indexes),
                    "reason": skip_reason or "no-candidate-passed-all-gates",
                }
            )
        return {
            "status": "passed-with-warnings" if warnings else "passed",
            "policy": "lexical-guarded-transactional",
            "errors": [],
            "warnings": warnings,
            "metrics": {
                "wordCount": len(words),
                "flaggedWordCount": len(flagged),
                "residualActivityIntervalCount": len(residual_intervals),
                "unalignedResidualActivityIntervalCount": sum(
                    bool(item["unaligned"]) for item in residual_intervals
                ),
                "confirmedUnalignedResidualActivityIntervalCount": len(
                    confirmed_residual_intervals
                ),
                "cleanedUnalignedResidualActivityIntervalCount": len(
                    cleaned_residual_intervals
                ),
                "remainingConfirmedUnalignedResidualActivityIntervalCount": (
                    unresolved_confirmed_intervals
                ),
                "reviewSpectralOverlapDb": review_threshold,
                "residualActivityReviewSpectralOverlapDb": scan_threshold,
                "targetedWordRefinementCount": len(target_word_indexes),
                "targetedWordRefinementRatio": round(target_ratio, 4),
                "refinementApplied": refinement_applied,
                "naturalStemPreserved": not refinement_applied,
                "acceptedCandidate": accepted_metrics,
            },
            "flaggedWords": flagged,
            "wordAudit": words,
            "preLexicalResidualAudit": pre_lexical_audit,
            "lexicalResidualAudit": {
                "status": "superseded-by-two-model-consensus",
                "matchedResidualWordCount": len(target_word_indexes),
                "matchedResidualWords": [
                    words[index] for index in target_word_indexes
                ],
            },
            "residualConsensus": consensus_report,
            "residualIntervalConsensus": interval_consensus_report,
            "guardedCleanupAttempts": attempts,
            "guardedIntervalCleanupAttempts": interval_cleanup_attempts,
            "terminalResidualActions": [],
            "residualActivityIntervals": residual_intervals,
        }
    if refinement_mode != "bounded":
        raise ValueError(
            "Unsupported lyric-leakage refinement mode: " + refinement_mode
        )

    n_fft = int(settings.get("refinementFftSize", 4_096))
    hop = int(settings.get("refinementHopSize", 1_024))
    strength = float(settings.get("refinementStrength", 1.0))
    residual_strength = float(
        settings.get("residualActivityRefinementStrength", 1.0)
    )
    residual_magnitude_strength = float(
        settings.get("residualActivityMagnitudeRefinementStrength", 1.0)
    )
    fade = float(settings.get("refinementFadeSeconds", 0.12))
    lower_ratio = float(settings.get("vocalDominanceLowerRatio", 0.5))
    ratio_span = float(settings.get("vocalDominanceRatioSpan", 2.5))
    projection_minimum_coherence = float(
        settings.get("residualProjectionMinimumCoherence", 0.15)
    )
    projection_coherence_span = float(
        settings.get("residualProjectionCoherenceSpan", 0.35)
    )
    projection_maximum_coefficient = float(
        settings.get("residualProjectionMaximumCoefficient", 0.85)
    )
    projection_smoothing_frames = max(
        1, int(settings.get("residualProjectionSmoothingFrames", 5))
    )
    refinement_passes = max(1, int(settings.get("refinementPasses", 2)))
    configured_aligned_refinement_passes = max(
        0,
        min(
            refinement_passes,
            int(settings.get("alignedRefinementPasses", 1)),
        ),
    )
    aligned_projection_strength = float(
        settings.get("alignedProjectionStrength", 1.0)
    )
    native_frequencies = librosa.fft_frequencies(sr=sample_rate, n_fft=n_fft)
    native_band = (
        (native_frequencies >= minimum_hz)
        & (native_frequencies <= float(settings.get("refinementMaximumHz", 8_000.0)))
    ).astype("float32")[:, None]
    refined = np.empty_like(instrumental)
    # Keep the aligned-word pass count independently configurable. Production
    # can preserve already-clean lyric phrases while still applying repeated
    # cleanup to unaligned calls, harmonies, and reverb tails.
    targeted_word_indexes = set(lexical_target_word_indexes)
    boundary_word_tolerance = float(
        settings.get("boundaryWordRefinementToleranceSeconds", 0.18)
    )
    for residual in residual_intervals:
        boundary = residual.get("lyricBoundary")
        if boundary == "onset":
            candidates = [
                (abs(float(word["start"]) - float(residual["end"])), index)
                for index, word in enumerate(words)
                if abs(float(word["start"]) - float(residual["end"]))
                <= boundary_word_tolerance
            ]
        elif boundary == "end":
            candidates = [
                (abs(float(word["end"]) - float(residual["start"])), index)
                for index, word in enumerate(words)
                if abs(float(word["end"]) - float(residual["start"]))
                <= boundary_word_tolerance
            ]
        else:
            candidates = []
        if candidates:
            targeted_word_indexes.add(min(candidates)[1])
    if configured_aligned_refinement_passes:
        aligned_refinement_intervals = merge_intervals(
            lyric_intervals,
            maximum_gap=float(
                settings.get("refinementIntervalMergeGapSeconds", 0.04)
            ),
        )
        aligned_refinement_passes = configured_aligned_refinement_passes
    else:
        aligned_refinement_intervals = merge_intervals(
            [
                (float(words[index]["start"]), float(words[index]["end"]))
                for index in sorted(targeted_word_indexes)
            ],
            maximum_gap=float(
                settings.get("refinementIntervalMergeGapSeconds", 0.04)
            ),
        )
        aligned_refinement_passes = min(
            refinement_passes,
            max(
                0,
                int(
                    settings.get(
                        "lexicalTargetedRefinementPasses", refinement_passes
                    )
                ),
            ),
        )
    residual_refinement_intervals = merge_intervals(
        [
            (float(item["start"]), float(item["end"]))
            for item in residual_intervals
        ],
        maximum_gap=float(settings.get("refinementIntervalMergeGapSeconds", 0.04)),
    )
    for channel in range(instrumental.shape[1]):
        instrument_stft = librosa.stft(
            instrumental[:, channel], n_fft=n_fft, hop_length=hop, window="hann"
        )
        vocal_stft = librosa.stft(
            vocals[:, channel], n_fft=n_fft, hop_length=hop, window="hann"
        )
        times = librosa.frames_to_time(
            np.arange(instrument_stft.shape[1]), sr=sample_rate, hop_length=hop
        )
        def interval_envelope(start: float, end: float) -> Any:
            return np.where(
                (times >= start) & (times <= end),
                1.0,
                np.where(
                    (times >= start - fade) & (times < start),
                    (times - (start - fade)) / fade,
                    np.where(
                        (times > end) & (times <= end + fade),
                        ((end + fade) - times) / fade,
                        0.0,
                    ),
                ),
            ).astype("float32")

        def interval_weight(active_intervals: list[tuple[float, float]]) -> Any:
            weight = np.zeros(instrument_stft.shape[1], dtype="float32")
            for start, end in active_intervals:
                weight = np.maximum(weight, interval_envelope(start, end))
            return weight

        aligned_time_weight = interval_weight(aligned_refinement_intervals)
        residual_time_weight = interval_weight(residual_refinement_intervals)
        # A single transfer coefficient over an entire phrase only removes the
        # average leakage.  Consonants, calls, and reverb tails change much
        # faster, so estimate a locally smoothed complex transfer function per
        # time-frequency bin.  Smoothing keeps unrelated instruments from
        # being mistaken for a vocal while following real leakage closely.
        from scipy.ndimage import uniform_filter1d

        current_instrument_stft = instrument_stft.copy()
        current_vocal_stft = vocal_stft.copy()
        for pass_index in range(refinement_passes):
            active_aligned_weight = (
                aligned_time_weight
                if pass_index < aligned_refinement_passes
                else np.zeros_like(aligned_time_weight)
            )
            projection_time_weight = np.maximum(
                aligned_projection_strength * active_aligned_weight,
                residual_strength * residual_time_weight,
            )
            magnitude_time_weight = np.maximum(
                strength * active_aligned_weight,
                residual_magnitude_strength * residual_time_weight,
            )
            vocal_to_instrumental = np.abs(current_vocal_stft) / (
                np.abs(current_instrument_stft) + 1e-7
            )
            dominance = np.clip(
                (vocal_to_instrumental - lower_ratio) / ratio_span,
                0.0,
                1.0,
            )
            local_cross = uniform_filter1d(
                current_instrument_stft * np.conj(current_vocal_stft),
                size=projection_smoothing_frames,
                axis=1,
                mode="nearest",
            )
            local_vocal_energy = uniform_filter1d(
                np.abs(current_vocal_stft) ** 2,
                size=projection_smoothing_frames,
                axis=1,
                mode="nearest",
            )
            local_instrumental_energy = uniform_filter1d(
                np.abs(current_instrument_stft) ** 2,
                size=projection_smoothing_frames,
                axis=1,
                mode="nearest",
            )
            local_coherence = np.abs(local_cross) ** 2 / (
                local_vocal_energy * local_instrumental_energy + 1e-12
            )
            local_coherence_weight = np.clip(
                (local_coherence - projection_minimum_coherence)
                / projection_coherence_span,
                0.0,
                1.0,
            )
            local_coefficient = local_cross / (local_vocal_energy + 1e-12)
            local_coefficient_magnitude = np.abs(local_coefficient)
            local_coefficient *= np.minimum(
                1.0,
                projection_maximum_coefficient
                / (local_coefficient_magnitude + 1e-12),
            )
            projection = (
                local_coefficient
                * local_coherence_weight
                * native_band
                * current_vocal_stft
            )
            projected_instrument = current_instrument_stft - (
                projection * projection_time_weight[None, :]
            )
            gain = 1.0 - (
                dominance
                * native_band
                * magnitude_time_weight[None, :]
            )
            gain = np.clip(gain, 0.0, 1.0)
            next_instrument_stft = projected_instrument * gain
            current_vocal_stft += (
                current_instrument_stft - next_instrument_stft
            )
            current_instrument_stft = next_instrument_stft
        refined[:, channel] = librosa.istft(
            current_instrument_stft,
            hop_length=hop,
            window="hann",
            length=len(instrumental),
        ).astype("float32")

    terminal_actions: list[dict[str, Any]] = []
    terminal_policy = str(
        settings.get("terminalResidualPolicy", "fade-out")
    ).strip()
    if terminal_policy == "fade-out" and words and residual_intervals:
        final_word_end = max(float(item["end"]) for item in words)
        duration_seconds = len(refined) / sample_rate
        terminal_guard = float(
            settings.get("terminalResidualGuardSeconds", 0.12)
        )
        terminal_tail = float(
            settings.get("terminalResidualMaximumTailSeconds", 1.0)
        )
        fade_seconds = float(settings.get("terminalResidualFadeSeconds", 0.35))
        for item in residual_intervals:
            start = float(item["start"])
            end = float(item["end"])
            if (
                start >= final_word_end - terminal_guard
                and duration_seconds - end <= terminal_tail
            ):
                fade_start = max(final_word_end, start)
                sample_start = min(len(refined), round(fade_start * sample_rate))
                sample_end = min(
                    len(refined),
                    sample_start + max(1, round(fade_seconds * sample_rate)),
                )
                envelope = np.ones(len(refined), dtype="float32")
                envelope[sample_start:sample_end] = np.linspace(
                    1.0, 0.0, sample_end - sample_start, dtype="float32"
                )
                envelope[sample_end:] = 0.0
                refined *= envelope[:, None]
                terminal_actions.append(
                    {
                        "policy": terminal_policy,
                        "start": round(fade_start, 3),
                        "fadeSeconds": round(fade_seconds, 3),
                    }
                )
                break

    difference = instrumental - refined
    refined_vocals = vocals + difference
    change_energy = float(np.sum(difference.astype("float64") ** 2))
    instrumental_energy = float(np.sum(instrumental.astype("float64") ** 2))
    preservation_snr = 10.0 * math.log10(
        max(instrumental_energy, 1e-20) / max(change_energy, 1e-20)
    )
    protected = np.ones(len(instrumental), dtype=bool)
    protection_padding = fade + n_fft / sample_rate
    for start, end in [
        *aligned_refinement_intervals,
        *residual_refinement_intervals,
    ]:
        sample_start = max(0, int((start - protection_padding) * sample_rate))
        sample_end = min(
            len(protected), int(math.ceil((end + protection_padding) * sample_rate))
        )
        protected[sample_start:sample_end] = False
    # A terminal fade is an explicit cleanup target too. Excluding only the
    # detected residual interval would misclassify the intentional fade tail
    # as collateral damage to otherwise untargeted music.
    for action in terminal_actions:
        sample_start = max(
            0,
            int(
                (float(action["start"]) - protection_padding)
                * sample_rate
            ),
        )
        protected[sample_start:] = False
    untargeted_change_energy = float(
        np.sum(difference[protected].astype("float64") ** 2)
    )
    untargeted_instrumental_energy = float(
        np.sum(instrumental[protected].astype("float64") ** 2)
    )
    untargeted_preservation_snr = 10.0 * math.log10(
        max(untargeted_instrumental_energy, 1e-20)
        / max(untargeted_change_energy, 1e-20)
    )
    refined_analysis = analysis_audio(refined)
    minimum_improvement = float(settings.get("minimumFlaggedWordImprovementDb", 0.5))
    maximum_overlap = float(settings.get("maximumSpectralOverlapDb", -4.0))
    maximum_residual_coherent_leakage = float(
        settings.get("maximumResidualActivityCoherentLeakageDb", -24.0)
    )
    for item in words:
        post = word_overlap_db(
            refined_analysis, float(item["start"]), float(item["end"])
        )
        item["postSpectralOverlapDb"] = round(post, 4)
        item["improvementDb"] = round(
            float(item["preSpectralOverlapDb"]) - post, 4
        )
        post_coherent = coherent_leakage_db(
            refined_analysis, float(item["start"]), float(item["end"])
        )
        item["postCoherentLeakageDb"] = round(post_coherent, 4)
        item["coherentImprovementDb"] = round(
            float(item["preCoherentLeakageDb"]) - post_coherent, 4
        )
    for item in residual_intervals:
        post = word_overlap_db(
            refined_analysis, float(item["start"]), float(item["end"])
        )
        item["postSpectralOverlapDb"] = round(post, 4)
        item["improvementDb"] = round(
            float(item["preSpectralOverlapDb"]) - post, 4
        )
        post_coherent = coherent_leakage_db(
            refined_analysis, float(item["start"]), float(item["end"])
        )
        item["postCoherentLeakageDb"] = round(post_coherent, 4)
        item["coherentImprovementDb"] = round(
            float(item["preCoherentLeakageDb"]) - post_coherent, 4
        )
    lexical_audit: dict[str, Any] = {
        "status": "disabled",
        "errors": [],
        "matchedResidualWordCount": 0,
        "matchedResidualWords": [],
    }
    if lexical_enabled and project_root is not None:
        audit_path = instrumental_path.with_suffix(".lexical-audit.partial.flac")
        try:
            sf.write(
                audit_path,
                refined,
                sample_rate,
                format="FLAC",
                subtype="PCM_24",
            )
            assert lexical_aligner is not None
            lexical_audit, _ = lexical_residual_audit(
                lexical_aligner.load_audio(audit_path),
                coherent_metric="postCoherentLeakageDb",
            )
        except Exception as exc:
            lexical_audit = {
                "status": "failed",
                "errors": [
                    {
                        "code": "LEXICAL_RESIDUAL_AUDIT_FAILED",
                        "message": str(exc),
                    }
                ],
                "matchedResidualWordCount": 0,
                "matchedResidualWords": [],
            }
        finally:
            audit_path.unlink(missing_ok=True)
    if lexical_aligner is not None:
        del lexical_aligner
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    worst_word_post = max(
        (float(item["postCoherentLeakageDb"]) for item in words),
        default=-120.0,
    )
    worst_residual_coherent_post = max(
        (float(item["postCoherentLeakageDb"]) for item in residual_intervals),
        default=-120.0,
    )
    maximum_word_coherent_leakage = float(
        settings.get("maximumWordCoherentLeakageDb", -30.0)
    )
    lexical_audit_completed = lexical_audit["status"] in {"passed", "failed"}
    if (
        not lexical_audit_completed
        and worst_word_post > maximum_word_coherent_leakage
    ):
        errors.append(
            {
                "code": "LYRIC_LEAKAGE_REMAINS_HIGH",
                "observedDb": round(worst_word_post, 4),
                "maximumDb": maximum_word_coherent_leakage,
            }
        )
    weak_word_improvements = [
        item
        for item in words
        if float(item["preCoherentLeakageDb"])
        > maximum_word_coherent_leakage
        and float(item["coherentImprovementDb"]) < minimum_improvement
    ]
    if weak_word_improvements and not lexical_audit_completed:
        errors.append(
            {
                "code": "LYRIC_LEAKAGE_REFINEMENT_INEFFECTIVE",
                "observedWords": len(weak_word_improvements),
                "minimumImprovementDb": minimum_improvement,
            }
        )
    lexical_policy = str(
        settings.get("lexicalResidualAuditPolicy", "strict")
    ).strip()
    pre_lexical_count = int(
        pre_lexical_audit.get("matchedResidualWordCount", 0)
    )
    post_lexical_count = int(
        lexical_audit.get("matchedResidualWordCount", 0)
    )
    lexical_reduction_ratio = (
        (pre_lexical_count - post_lexical_count) / pre_lexical_count
        if pre_lexical_count > 0
        else 1.0 if post_lexical_count == 0 else 0.0
    )
    lexical_audit_failures = [
        item
        for item in lexical_audit["errors"]
        if item.get("code") != "LEXICAL_VOCAL_RESIDUAL_DETECTED"
    ]
    errors.extend(lexical_audit_failures)
    if post_lexical_count:
        if lexical_policy == "strict":
            errors.extend(
                item
                for item in lexical_audit["errors"]
                if item.get("code") == "LEXICAL_VOCAL_RESIDUAL_DETECTED"
            )
        elif lexical_policy == "improvement-gate":
            minimum_lexical_reduction = float(
                settings.get("minimumLexicalResidualReductionRatio", 0.6)
            )
            if lexical_reduction_ratio < minimum_lexical_reduction:
                errors.append(
                    {
                        "code": "LEXICAL_VOCAL_RESIDUAL_REDUCTION_LOW",
                        "observedRatio": round(lexical_reduction_ratio, 4),
                        "minimumRatio": minimum_lexical_reduction,
                        "remainingWords": post_lexical_count,
                    }
                )
            else:
                warnings.append(
                    {
                        "code": "LEXICAL_VOCAL_RESIDUAL_REMAINS",
                        "initialWords": pre_lexical_count,
                        "remainingWords": post_lexical_count,
                        "reductionRatio": round(lexical_reduction_ratio, 4),
                    }
                )
        else:
            errors.append(
                {
                    "code": "LEXICAL_RESIDUAL_AUDIT_POLICY_INVALID",
                    "policy": lexical_policy,
                }
            )
    maximum_boundary_coherent_leakage = float(
        settings.get("maximumLyricBoundaryCoherentLeakageDb", -27.0)
    )
    remaining_residuals = [
        item
        for item in residual_intervals
        if (
            float(item["postCoherentLeakageDb"])
            > (
                maximum_boundary_coherent_leakage
                if item.get("lyricBoundary") == "onset"
                else maximum_residual_coherent_leakage
            )
        )
    ]
    if remaining_residuals:
        errors.append(
            {
                "code": "UNALIGNED_VOCAL_RESIDUAL_NOT_CLEAN_OR_REMOVED",
                "observedIntervals": len(remaining_residuals),
                "maximumRemainingCoherentLeakageDb": (
                    maximum_residual_coherent_leakage
                ),
            }
        )
    minimum_preservation = float(settings.get("minimumMusicPreservationSnrDb", 32.0))
    if untargeted_preservation_snr < minimum_preservation:
        errors.append(
            {
                "code": "LYRIC_LEAKAGE_MUSIC_PRESERVATION_LOW",
                "observedDb": round(untargeted_preservation_snr, 4),
                "minimumDb": minimum_preservation,
            }
        )
    minimum_global_preservation = float(
        settings.get("minimumGlobalMusicPreservationSnrDb", -120.0)
    )
    if preservation_snr < minimum_global_preservation:
        errors.append(
            {
                "code": "LYRIC_LEAKAGE_GLOBAL_MUSIC_PRESERVATION_LOW",
                "observedDb": round(preservation_snr, 4),
                "minimumDb": minimum_global_preservation,
            }
        )
    targeted_word_ratio = len(targeted_word_indexes) / max(len(words), 1)
    maximum_targeted_word_ratio = float(
        settings.get("maximumTargetedWordRefinementRatio", 1.0)
    )
    if targeted_word_ratio > maximum_targeted_word_ratio:
        errors.append(
            {
                "code": "LYRIC_LEAKAGE_TARGET_SCOPE_TOO_BROAD",
                "observedRatio": round(targeted_word_ratio, 4),
                "maximumRatio": maximum_targeted_word_ratio,
            }
        )
    maximum_peak = float(settings.get("maximumSamplePeak", 0.999))
    peaks = {
        "instrumental": float(np.max(np.abs(refined))),
        "vocals": float(np.max(np.abs(refined_vocals))),
    }
    if max(peaks.values()) > maximum_peak:
        errors.append(
            {
                "code": "LYRIC_LEAKAGE_REFINEMENT_PEAK_HIGH",
                "observed": round(max(peaks.values()), 6),
                "maximum": maximum_peak,
            }
        )
    if not errors:
        instrumental_partial = instrumental_path.with_suffix(".refined.partial.flac")
        vocal_partial = vocal_path.with_suffix(".refined.partial.flac")
        sf.write(
            instrumental_partial,
            refined,
            sample_rate,
            format="FLAC",
            subtype="PCM_24",
        )
        sf.write(
            vocal_partial,
            refined_vocals,
            sample_rate,
            format="FLAC",
            subtype="PCM_24",
        )
        instrumental_partial.replace(instrumental_path)
        vocal_partial.replace(vocal_path)
    return {
        "status": (
            "failed"
            if errors
            else "passed-with-warnings"
            if warnings
            else "passed"
        ),
        "policy": residual_policy,
        "errors": errors,
        "warnings": warnings,
        "metrics": {
            "wordCount": len(words),
            "flaggedWordCount": len(flagged),
            "residualActivityIntervalCount": len(residual_intervals),
            "unalignedResidualActivityIntervalCount": sum(
                bool(item["unaligned"]) for item in residual_intervals
            ),
            "reviewSpectralOverlapDb": review_threshold,
            "residualActivityReviewSpectralOverlapDb": scan_threshold,
            "maximumSpectralOverlapDb": maximum_overlap,
            "maximumWordCoherentLeakageDb": maximum_word_coherent_leakage,
            "maximumResidualActivityCoherentLeakageDb": (
                maximum_residual_coherent_leakage
            ),
            "maximumLyricBoundaryCoherentLeakageDb": (
                maximum_boundary_coherent_leakage
            ),
            "worstWordPostCoherentLeakageDb": round(worst_word_post, 4),
            "worstResidualActivityPostCoherentLeakageDb": round(
                worst_residual_coherent_post, 4
            ),
            "musicPreservationSnrDb": round(preservation_snr, 4),
            "untargetedMusicPreservationSnrDb": round(
                untargeted_preservation_snr, 4
            ),
            "minimumGlobalMusicPreservationSnrDb": (
                minimum_global_preservation
            ),
            "samplePeaks": {name: round(value, 6) for name, value in peaks.items()},
            "refinementApplied": not errors,
            "targetedWordRefinementCount": len(targeted_word_indexes),
            "targetedWordRefinementRatio": round(targeted_word_ratio, 4),
            "alignedRefinementPasses": aligned_refinement_passes,
            "lexicalResidualReductionRatio": round(
                lexical_reduction_ratio, 4
            ),
        },
        "flaggedWords": flagged,
        "wordAudit": words,
        "preLexicalResidualAudit": pre_lexical_audit,
        "lexicalResidualAudit": lexical_audit,
        "terminalResidualActions": terminal_actions,
        "residualActivityIntervals": residual_intervals,
    }


def _input_lyrics_snapshot(context: StageContext) -> Path:
    return context.job_directory / "inputs" / "lyrics.txt"


def _lyrics(context: StageContext) -> Path:
    return _shared_work(context) / "lyrics.json"


def _aligned_lyrics(context: StageContext) -> Path:
    """Immutable role-classification input for idempotent stage retries."""
    return _shared_work(context) / "aligned-lyrics.json"


def _ass(context: StageContext) -> Path:
    return context.artifacts_directory / "karaoke.ass"


def _karaoke_render_plan(context: StageContext) -> Path:
    return context.artifacts_directory / "karaoke-render-plan.json"


def _load_lyrics(context: StageContext) -> list[dict[str, Any]]:
    """Load the immutable user lyric snapshot; never infer or replace text."""
    context.progress(10, "Validating authoritative lyric input")
    job = context.store.load(context.job_id)
    lyric_request = job.get("request", {}).get("lyrics", {})
    if lyric_request.get("mode") != "authoritative-input":
        raise ValueError("This job has no authoritative lyric input.")
    configured_snapshot = str(lyric_request.get("snapshot", "")).strip()
    expected_snapshot = _input_lyrics_snapshot(context).resolve()
    snapshot = (context.job_directory / configured_snapshot).resolve()
    if snapshot != expected_snapshot or context.job_directory.resolve() not in snapshot.parents:
        raise ValueError("The job lyric snapshot path is invalid.")
    if not snapshot.is_file():
        raise ValueError(f"The job lyric snapshot is missing: {snapshot}")
    text, lines = normalize_authoritative_lyrics(snapshot.read_bytes().decode("utf-8"))
    observed_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    expected_hash = str(lyric_request.get("sha256", "")).strip()
    if not expected_hash or observed_hash != expected_hash:
        raise ValueError("The authoritative lyric snapshot failed its integrity check.")
    word_count = sum(len(line.split()) for line in lines)
    if int(lyric_request.get("lineCount", -1)) != len(lines) or int(
        lyric_request.get("wordCount", -1)
    ) != word_count:
        raise ValueError("The authoritative lyric snapshot metadata is inconsistent.")
    payload = {
        "schemaVersion": 1,
        "mode": "authoritative-input",
        "sourcePath": lyric_request.get("sourcePath"),
        "snapshot": configured_snapshot,
        "sha256": observed_hash,
        "lineCount": len(lines),
        "wordCount": word_count,
        "detectedTextUsed": False,
        "captionUsed": False,
        "lines": [
            {"index": index, "text": line}
            for index, line in enumerate(lines, start=1)
        ],
    }
    output = _authoritative_lyrics_file(context)
    atomic_write_json(output, payload)
    context.log(
        f"Loaded {word_count} exact lyric words from the immutable user snapshot; "
        "text detection is disabled."
    )
    context.progress(100, f"Loaded {len(lines)} authoritative lyric lines")
    return [_artifact(output, "lyrics-source", "Authoritative lyric input")]


def apply_embedded_media_metadata(
    metadata: dict[str, Any], probe_data: dict[str, Any]
) -> bool:
    source_metadata = metadata.setdefault("source", {})
    format_tags = {
        str(key).casefold(): str(value).strip()
        for key, value in probe_data.get("format", {}).get("tags", {}).items()
        if str(value).strip()
    }
    tag_fields = {
        "songTitle": ("title",),
        "referenceArtist": ("artist", "album_artist", "albumartist"),
        "composer": ("composer",),
    }
    changed = False
    for destination, candidates in tag_fields.items():
        if str(source_metadata.get(destination, "")).strip():
            continue
        value = next((format_tags[key] for key in candidates if key in format_tags), "")
        if value:
            source_metadata[destination] = value
            changed = True
    if changed:
        source_metadata["identityMethod"] = "embedded-media-tags"
    return changed


def _probe(context: StageContext) -> list[dict[str, Any]]:
    root = _project_root(context)
    source = _source_media(context)
    context.progress(10, f"Probing {source.name}")
    result = _run(
        context,
        [
            _ffprobe(root),
            "-v",
            "error",
            "-show_format",
            "-show_streams",
            "-of",
            "json",
            str(source),
        ],
        progress=70,
    )
    data = json.loads(result.stdout)
    video_stream = next(
        (stream for stream in data.get("streams", []) if stream.get("codec_type") == "video"),
        None,
    )
    audio_stream = next(
        (stream for stream in data.get("streams", []) if stream.get("codec_type") == "audio"),
        None,
    )
    if not audio_stream:
        raise ValueError("The source must contain an audio stream.")
    metadata_path = context.job_directory / "metadata.json"
    metadata = load_json(metadata_path) if metadata_path.is_file() else {"source": {}}
    if apply_embedded_media_metadata(metadata, data):
        atomic_write_json(metadata_path, metadata)
    directives = load_source_directives(source)
    duration = float(data.get("format", {}).get("duration") or 0)
    request = _job(context).get("request", {})
    requested_media_trim = request.get("mediaTrim", {})
    directive_trim = directives.get("trim", {})
    requested_start = requested_media_trim.get("startSeconds")
    requested_end = requested_media_trim.get("endSeconds")
    trim_start = max(
        0.0,
        float(
            requested_start
            if requested_start is not None
            else directive_trim.get("startSeconds", 0)
        ),
    )
    trim_end = min(
        duration,
        float(
            requested_end
            if requested_end is not None
            else directive_trim.get("endSeconds", duration)
        ),
    )
    if trim_end <= trim_start:
        raise ValueError("The trim range in the sidecar is invalid.")
    data["lyricRail"] = {
        "hasVideo": video_stream is not None,
        "sourceKind": "video" if video_stream is not None else "audio",
        "sourceRange": request.get("sourceRange", {}),
        "sourcePretrimmed": bool(request.get("sourcePretrimmed", False)),
        "trimStartSeconds": trim_start,
        "trimEndSeconds": trim_end,
        "outputDurationSeconds": trim_end - trim_start,
        "directives": directives,
    }
    path = _probe_file(context)
    atomic_write_json(path, data)
    context.progress(100, f"Source duration {duration:.3f}s; output {trim_end-trim_start:.3f}s")
    return [_artifact(path, "analysis", "Source media probe")]


def _extract_audio(context: StageContext) -> list[dict[str, Any]]:
    root = _project_root(context)
    source = _source_media(context)
    probe = load_json(_probe_file(context))["lyricRail"]
    output = _source_audio(context)
    context.progress(5, "Extracting trimmed 24-bit/48 kHz audio")
    _run(
        context,
        [
            _ffmpeg(root),
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-y",
            "-ss",
            f"{probe['trimStartSeconds']:.6f}",
            "-to",
            f"{probe['trimEndSeconds']:.6f}",
            "-i",
            str(source),
            "-map",
            "0:a:0",
            "-vn",
            "-c:a",
            "pcm_s24le",
            "-ar",
            "48000",
            "-ac",
            "2",
            str(output),
        ],
        progress=100,
    )
    return [_artifact(output, "audio-source", "Trimmed lossless source audio")]


def _separate_stems(context: StageContext) -> list[dict[str, Any]]:
    root = _project_root(context)
    config = load_project_config(root)["pipeline"].get("audioSeparation", {})
    _prepend_process_path(Path(_ffmpeg(root)).parent)
    cuda_available = _prepare_cuda_runtime()
    context.log(f"PyTorch CUDA available: {cuda_available}")
    try:
        from audio_separator.separator import Separator
    except ImportError as exc:
        raise RuntimeError(
            "audio-separator is missing. Run scripts/install.py with the separation-gpu extra."
        ) from exc

    model_dir = root / "models" / "audio-separator"
    model_dir.mkdir(parents=True, exist_ok=True)
    output_dir = _shared_work(context)
    model = str(
        config.get("modelFilename", "model_bs_roformer_ep_317_sdr_12.9755.ckpt")
    )
    ensemble_preset = str(config.get("ensemblePreset", "")).strip() or None
    model_label = f"ensemble:{ensemble_preset}" if ensemble_preset else model
    context.progress(5, f"Loading separation model {model_label}")
    # PyTorch 2.6+ defaults torch.load to weights_only=True. Some official
    # audio-separator RoFormer checkpoints use the legacy OrderedDict pickle
    # encoding and fail inside the restricted unpickler. The model is fetched
    # pinned by SHA-256 in model-manifest.json and verified before every run, so
    # scope the compatibility fallback to model loading and restore torch.load
    # immediately afterwards.
    import torch

    original_torch_load = torch.load

    def compatible_torch_load(*args: Any, **kwargs: Any) -> Any:
        kwargs.setdefault("weights_only", False)
        return original_torch_load(*args, **kwargs)

    torch.load = compatible_torch_load
    try:
        def execute(preset: str | None) -> tuple[Any, list[str]]:
            sample_rate = int(config.get("sampleRate", 48000))
            use_autocast = bool(config.get("useAutocast", True))
            cache_key = (
                str(model_dir.resolve()),
                model,
                preset or "",
                sample_rate,
                use_autocast,
            )
            persistent_worker = os.environ.get("LYRICRAIL_PERSISTENT_WORKER") == "1"
            active = _SEPARATION_MODEL_CACHE.get(cache_key) if persistent_worker else None
            if active is None:
                active = Separator(
                    output_dir=str(output_dir),
                    model_file_dir=str(model_dir),
                    output_format="FLAC",
                    sample_rate=sample_rate,
                    use_autocast=use_autocast,
                    ensemble_preset=preset,
                )
                if preset:
                    active.load_model()
                else:
                    active.load_model(model_filename=model)
                if persistent_worker:
                    _SEPARATION_MODEL_CACHE[cache_key] = active
            else:
                context.log(f"Reusing loaded separation model {model_label}")
                active.output_dir = str(output_dir)
            context.progress(15, "Separating vocals and instrumental")
            separated = active.separate(
                str(_source_audio(context)),
                {
                    "Vocals": "vocals",
                    "Instrumental": "instrumental",
                    "vocals": "vocals",
                    "other": "instrumental",
                },
            )
            return active, separated

        try:
            separator, outputs = execute(ensemble_preset)
        except Exception as exc:
            if not ensemble_preset or not bool(config.get("allowModelFallback", False)):
                raise
            context.log(
                f"Karaoke ensemble failed; retrying fallback model {model}: {exc}",
                "WARNING",
            )
            for stem in ("vocals", "instrumental"):
                for partial in output_dir.glob(stem + ".*"):
                    partial.unlink(missing_ok=True)
            gc.collect()
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
            separator, outputs = execute(None)
    finally:
        torch.load = original_torch_load
    context.checkpoint()
    for expected in (_vocals(context), _instrumental(context)):
        if not expected.is_file():
            matches = list(output_dir.glob(expected.stem + ".*"))
            if not matches:
                raise RuntimeError(
                    f"Separation completed but expected stem is missing: {expected.name}; "
                    f"outputs={outputs}"
                )
            shutil.move(str(matches[0]), str(expected))
    quality = stem_separation_qc(
        _source_audio(context),
        _instrumental(context),
        _vocals(context),
        config.get("qualityGates", {}),
    )
    quality.update(
        {
            "target": str(config.get("target", "all-vocals")),
            "modelPolicy": str(config.get("modelPolicy", "")),
            "model": model,
            "ensemblePreset": ensemble_preset,
        }
    )
    atomic_write_json(_stem_qc_file(context), quality)
    context.log("Stem separation quality control: " + json.dumps(quality))
    if quality["errors"]:
        raise ValueError(
            "Stem separation failed production quality control: "
            + json.dumps(quality["errors"])
        )
    del separator
    context.progress(100, "Stem separation complete")
    return [
        _artifact(_instrumental(context), "instrumental", "Karaoke instrumental"),
        _artifact(_vocals(context), "vocals", "Isolated vocals"),
        _artifact(_stem_qc_file(context), "analysis", "Stem separation quality control"),
    ]


def _vietnamese_line_break_semantic_penalty(
    previous_text: str, following_text: str
) -> float:
    """Penalize boundaries that split Vietnamese grammatical/lexical units."""
    clean = lambda value: re.sub(
        r"[^0-9a-zà-ỹđ]+", "", value.casefold(), flags=re.IGNORECASE
    )
    previous = clean(previous_text)
    following = clean(following_text)
    bound_left = {
        "và",
        "hay",
        "hoặc",
        "nhưng",
        "mà",
        "thì",
        "là",
        "của",
        "cho",
        "với",
        "từ",
        "đến",
        "trong",
        "trên",
        "dưới",
        "bên",
        "nơi",
        "cùng",
        "những",
        "các",
        "mỗi",
        "mọi",
        "từng",
        "một",
        "cái",
        "chiếc",
        "cuộc",
        "cơn",
        "niềm",
        "nỗi",
        "tấm",
        "bức",
        "ngọn",
        "dòng",
        "mảnh",
        "đôi",
        "bao",
        "trăm",
        "ngàn",
        "vạn",
    }
    postmodifiers = {
        "xưa",
        "nay",
        "này",
        "kia",
        "ấy",
        "đó",
        "nào",
        "đầu",
        "cuối",
    }
    penalty = 0.0
    if previous in bound_left:
        penalty += 10.0
    if following in postmodifiers:
        penalty += 10.0
    return penalty


def _vietnamese_protected_word_boundaries(
    words: list[dict[str, Any]],
    *,
    constituency_analysis: dict[str, Any] | None = None,
) -> tuple[set[int], dict[int, float], list[dict[str, Any]]]:
    """Return syllable boundaries that split a Vietnamese lexical word.

    Karaoke timing treats each space-delimited Vietnamese syllable as a word,
    while Vietnamese NLP correctly groups many of those syllables into one
    lexical unit (for example ``bóng mát``, ``kết tóc`` or ``rung rung``).
    Display reflow must never cut inside such a unit.
    """
    def clean(value: str) -> str:
        return re.sub(
            r"[^0-9a-zà-ỹđ]+", "", value.casefold(), flags=re.IGNORECASE
        )

    original = [clean(str(word.get("text", ""))) for word in words]
    curated_lexical_units = {
        ("râm", "bóng", "mát"),
        ("kết", "tóc"),
        ("se", "duyên"),
        ("mộng", "chung", "đôi"),
        ("vỗ", "cánh"),
        ("âm", "thầm", "chuốt", "lấy"),
        ("mình", "tôi", "đứng"),
        ("yêu", "đương"),
        ("nhiệm", "màu"),
        ("giá", "buốt"),
        ("nhạt", "nhòa"),
        ("mãi", "mãi"),
        ("vu", "vơ"),
        ("thâm", "sâu"),
        ("thê", "lương"),
        ("sầu", "bi"),
        ("một", "mình"),
        ("suy", "tư"),
        ("đau", "thương"),
        ("mỏi", "mòn"),
        ("ca", "dao"),
        ("nước", "non"),
        ("chiến", "chinh"),
        ("chia", "phôi"),
        ("rung", "rung"),
        ("nghẹn", "ngào"),
        ("năm", "xưa"),
        ("màu", "xanh"),
        ("kỷ", "niệm"),
        ("nụ", "cười"),
    }
    if constituency_analysis is None:
        try:
            from underthesea import pos_tag
        except ImportError as exc:
            raise RuntimeError(
                "Vietnamese semantic lyric layout requires underthesea==9.5.0"
            ) from exc
        lexical_tokens = [
            (str(token), str(pos))
            for token, pos in pos_tag(
                " ".join(str(word.get("text", "")) for word in words)
            )
        ]
        constituent_token_spans: list[dict[str, Any]] = []
        syntax_tokens: list[dict[str, Any]] = []
    else:
        syntax_tokens = list(constituency_analysis.get("tokens", []))
        lexical_tokens = [
            (str(item["text"]), str(item.get("pos", "")))
            for item in syntax_tokens
        ]
        constituent_token_spans = list(
            constituency_analysis.get("constituents", [])
        )
    original_stream = "".join(original)
    word_char_spans: list[tuple[int, int]] = []
    char_cursor = 0
    for value in original:
        word_char_spans.append((char_cursor, char_cursor + len(value)))
        char_cursor += len(value)
    char_cursor = 0
    protected: set[int] = set()
    report: list[dict[str, Any]] = []

    def add_bounded_protection(candidate_boundaries: set[int]) -> bool:
        combined_boundaries = protected | candidate_boundaries
        longest_unit_words = 1
        run_boundaries = 0
        previous_boundary: int | None = None
        for boundary in sorted(combined_boundaries):
            run_boundaries = (
                run_boundaries + 1
                if previous_boundary is not None
                and boundary == previous_boundary + 1
                else 1
            )
            longest_unit_words = max(longest_unit_words, run_boundaries + 1)
            previous_boundary = boundary
        if longest_unit_words > 4:
            return False
        protected.update(candidate_boundaries)
        return True

    for unit in sorted(curated_lexical_units, key=len, reverse=True):
        size = len(unit)
        for start in range(0, len(original) - size + 1):
            if tuple(original[start : start + size]) != unit:
                continue
            protected.update(range(start + 1, start + size))
            report.append(
                {
                    "text": " ".join(
                        str(words[index].get("text", ""))
                        for index in range(start, start + size)
                    ),
                    "startWord": start + 1,
                    "endWord": start + size,
                    "kind": "curated-vietnamese-lexical-unit",
                }
            )
    curated_protected_boundaries = set(protected)
    for boundary in range(1, len(original)):
        if (
            original[boundary - 1]
            and original[boundary - 1] == original[boundary]
            and not PUNCTUATION_BREAK.search(
                str(words[boundary - 1].get("text", ""))
            )
        ):
            protected.add(boundary)
    word_pos: list[str | None] = [None] * len(original)
    token_word_spans: list[tuple[int, int]] = []
    for token, pos in lexical_tokens:
        token_text = clean(str(token))
        if not token_text:
            position = sum(end <= char_cursor for _, end in word_char_spans)
            token_word_spans.append((position, position))
            continue
        token_end = char_cursor + len(token_text)
        if original_stream[char_cursor:token_end] != token_text:
            raise ValueError(
                "Vietnamese word segmentation no longer maps losslessly to "
                f"the authoritative lyric at token {token!r}"
            )
        covered_words = [
            index
            for index, (start, end) in enumerate(word_char_spans)
            if start < token_end and end > char_cursor
        ]
        if not covered_words:
            raise ValueError(
                f"Vietnamese parser token {token!r} covers no lyric syllable"
            )
        start = covered_words[0]
        end = covered_words[-1] + 1
        for index in covered_words:
            if word_pos[index] is None:
                word_pos[index] = str(pos)
        if end - start > 1:
            token_parts = original[start:end]
            valid_lexical_unit = token_parts[0] not in {
                "à",
                "ạ",
                "ấy",
                "đó",
                "kia",
                "này",
                "nhé",
                "nhỉ",
                "ơi",
            }
            if valid_lexical_unit:
                candidate_boundaries = set(range(start + 1, end))
                extends_curated_unit = bool(
                    candidate_boundaries - curated_protected_boundaries
                ) and any(
                    abs(candidate - curated) == 1
                    for candidate in candidate_boundaries
                    for curated in curated_protected_boundaries
                )
                if not extends_curated_unit and add_bounded_protection(
                    candidate_boundaries
                ):
                    report.append(
                        {
                            "text": " ".join(
                                str(words[index].get("text", ""))
                                for index in range(start, end)
                            ),
                            "startWord": start + 1,
                            "endWord": end,
                        }
                    )
        token_word_spans.append((start, end))
        char_cursor = token_end
    if char_cursor != len(original_stream):
        raise ValueError(
            "Vietnamese word segmentation did not consume every authoritative "
            f"lyric character ({char_cursor}/{len(original_stream)})"
        )
    boundary_penalties: dict[int, float] = {}
    if constituent_token_spans:
        vocative_particles = {"à", "ạ", "ơi", "nhé", "nhỉ"}
        bound_postmodifiers = {
            "xưa",
            "nay",
            "này",
            "kia",
            "ấy",
            "đó",
            "nào",
            "đầu",
            "cuối",
        }
        for boundary in range(1, len(words)):
            if original[boundary] in vocative_particles:
                protected.add(boundary)
                report.append(
                    {
                        "text": " ".join(
                            str(words[index].get("text", ""))
                            for index in (boundary - 1, boundary)
                        ),
                        "startWord": boundary,
                        "endWord": boundary + 1,
                        "kind": "vocative-particle",
                    }
                )
            if original[boundary] in bound_postmodifiers or (
                original[boundary] == "nhau"
                and word_pos[boundary - 1] in {"ADJ", "ADV", "VERB"}
            ):
                protected.add(boundary)
        for constituent in constituent_token_spans:
            token_start = int(constituent["startToken"])
            token_end = int(constituent["endToken"])
            covered = [
                token_word_spans[index]
                for index in range(token_start, token_end)
                if token_word_spans[index][1] > token_word_spans[index][0]
            ]
            if not covered:
                continue
            start = min(item[0] for item in covered)
            end = max(item[1] for item in covered)
            size = end - start
            if size <= 1 or str(constituent.get("label", "")) == "ROOT":
                continue
            weight = 12.0 if size <= 3 else 8.0 if size <= 6 else 4.0
            for boundary in range(start + 1, end):
                boundary_penalties[boundary] = (
                    boundary_penalties.get(boundary, 0.0) + weight
                )
        tight_dependency_relations = {
            "amod",
            "clf",
            "compound",
            "det",
            "fixed",
            "flat",
            "acl",
        }
        dependency_weights = {
            "advmod": 60.0,
            "aux": 60.0,
            "case": 60.0,
            "cc": 60.0,
            "mark": 60.0,
            "ccomp": 50.0,
            "xcomp": 50.0,
            "obj": 20.0,
            "iobj": 20.0,
            "nsubj": 20.0,
        }
        for dependent_index, token in enumerate(syntax_tokens):
            head_index = int(token.get("head") or 0) - 1
            if not 0 <= head_index < len(token_word_spans):
                continue
            dependent_span = token_word_spans[dependent_index]
            head_span = token_word_spans[head_index]
            if dependent_span[0] == dependent_span[1] or head_span[0] == head_span[1]:
                continue
            start = min(dependent_span[0], head_span[0])
            end = max(dependent_span[1], head_span[1])
            full_relation = str(token.get("deprel", ""))
            relation = full_relation.split(":", 1)[0]
            tight_relation = (
                relation in tight_dependency_relations
                and (relation != "compound" or end - start <= 2)
            ) or (
                relation == "nmod"
                and str(token.get("pos", "")) == "NOUN"
                and str(syntax_tokens[head_index].get("pos", "")) == "NOUN"
            )
            adjacent_verbal_complement = (
                relation == "xcomp"
                and str(token.get("pos", "")) == "VERB"
                and str(syntax_tokens[head_index].get("pos", "")) == "VERB"
                and end - start <= 3
            )
            dependent_ends_clause = (
                dependent_span[1] == len(words)
                or (
                    dependent_span[1] > 0
                    and PUNCTUATION_BREAK.search(
                        str(words[dependent_span[1] - 1].get("text", ""))
                    )
                    is not None
                )
            )
            terminal_short_object = (
                relation in {"obj", "iobj"}
                and dependent_span[0] >= head_span[1]
                and end - start <= 2
                and dependent_ends_clause
            )
            tight_relation = (
                tight_relation
                or adjacent_verbal_complement
                or terminal_short_object
            )
            if tight_relation and end - start <= 4:
                candidate_boundaries = set(range(start + 1, end))
                add_bounded_protection(candidate_boundaries)
            weight = (
                100.0
                if tight_relation
                else dependency_weights.get(relation, 0.0)
            )
            if weight:
                for boundary in range(start + 1, end):
                    boundary_penalties[boundary] = (
                        boundary_penalties.get(boundary, 0.0) + weight
                    )
        universal_function_left = {"ADP", "AUX", "CCONJ", "DET", "SCONJ"}
        for boundary in range(1, len(words)):
            left_pos = word_pos[boundary - 1]
            right_pos = word_pos[boundary]
            if left_pos in universal_function_left:
                boundary_penalties[boundary] = (
                    boundary_penalties.get(boundary, 0.0) + 60.0
                )
            if left_pos == "ADV" and right_pos in {"ADV", "ADJ", "VERB"}:
                boundary_penalties[boundary] = (
                    boundary_penalties.get(boundary, 0.0) + 60.0
                )
            if right_pos in {"ADV", "AUX", "CCONJ", "PART"}:
                # A new display row must not begin with an aspect marker,
                # dependent adverb, conjunction, or grammatical particle.
                boundary_penalties[boundary] = (
                    boundary_penalties.get(boundary, 0.0) + 60.0
                )
            if left_pos in {"VERB", "ADJ"} and right_pos in {"NOUN", "PRON"}:
                boundary_penalties[boundary] = (
                    boundary_penalties.get(boundary, 0.0) + 18.0
                )
            if left_pos == "VERB" and right_pos == "ADJ":
                boundary_penalties[boundary] = (
                    boundary_penalties.get(boundary, 0.0) + 50.0
                )
            if left_pos == "VERB" and right_pos == "VERB":
                boundary_penalties[boundary] = (
                    boundary_penalties.get(boundary, 0.0) + 60.0
                )
            if left_pos in {"NOUN", "PRON"} and right_pos in {"ADJ", "PRON"}:
                boundary_penalties[boundary] = (
                    boundary_penalties.get(boundary, 0.0) + 18.0
                )
        return protected, boundary_penalties, report

    function_left = {"C", "E", "L", "M", "Nc"}
    nominal = {"N", "Nc", "Np", "P"}
    predicate = {"V", "A"}
    for boundary in range(1, len(words)):
        left_pos = word_pos[boundary - 1]
        right_pos = word_pos[boundary]
        penalty = 0.0
        if left_pos in function_left:
            penalty += 12.0
        if right_pos == "I":
            penalty += 12.0
        if left_pos in predicate and right_pos in nominal | {"A", "M"}:
            penalty += 12.0
        if left_pos in nominal and right_pos in nominal | {"A", "M"}:
            penalty += 10.0
        if penalty:
            boundary_penalties[boundary] = penalty
    return protected, boundary_penalties, report


def lyric_font_size_policy(
    layout: dict[str, Any], base_font_size: int
) -> tuple[bool, int, int]:
    """Resolve whether semantic reflow may trade font size for line length."""
    auto_shrink = bool(layout.get("autoShrinkLongLines", False))
    if not auto_shrink:
        return False, base_font_size, base_font_size
    minimum_font_size = int(layout.get("minimumFontSize", base_font_size))
    preferred_minimum_font_size = int(
        layout.get("preferredMinimumFontSize", base_font_size)
    )
    if not minimum_font_size <= preferred_minimum_font_size <= base_font_size:
        raise ValueError(
            "preferredMinimumFontSize must be between minimumFontSize "
            "and the base karaoke font size"
        )
    return True, minimum_font_size, preferred_minimum_font_size


def reflow_aligned_lyric_lines(
    lines: list[dict[str, Any]],
    *,
    maximum_words: int | None,
    target_words: int | None,
    minimum_words: int = 3,
    natural_pause_seconds: float = 0.18,
    measure_text: Callable[[str], float] | None = None,
    maximum_line_width: float | None = None,
    hard_maximum_line_width: float | None = None,
    semantic_analyzer: Callable[[str], dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Rewrap phrases by meaning and sung pauses, with visual width as safety."""
    width_mode = measure_text is not None and maximum_line_width is not None
    if width_mode and float(maximum_line_width) <= 0:
        raise ValueError("maximum_line_width must be positive")
    if width_mode and hard_maximum_line_width is not None:
        if float(hard_maximum_line_width) < float(maximum_line_width):
            raise ValueError(
                "hard_maximum_line_width cannot be smaller than maximum_line_width"
            )
    if not width_mode and (
        maximum_words is None
        or target_words is None
        or maximum_words < 1
        or target_words < 1
    ):
        raise ValueError("Legacy lyric wrapping limits must be positive")
    authoritative_word_stream = [
        str(syllable.get("text", ""))
        for line in lines
        for syllable in line.get("syllables", [])
    ]
    grouped: dict[int, list[dict[str, Any]]] = {}
    group_order: list[int] = []
    for index, line in enumerate(lines, start=1):
        group = int(line.get("referenceGroup", index))
        if group not in grouped:
            grouped[group] = []
            group_order.append(group)
        grouped[group].extend(dict(item) for item in line.get("syllables", []))

    output: list[dict[str, Any]] = []
    chosen_breaks: list[dict[str, Any]] = []
    protected_lexical_units: list[dict[str, Any]] = []
    inferred_semantic_boundaries: list[dict[str, Any]] = []
    orphan_line_count = 0
    for group in group_order:
        words = grouped[group]
        if not words:
            continue
        word_count = len(words)
        constituency_analysis = (
            semantic_analyzer(
                " ".join(str(word.get("text", "")) for word in words)
            )
            if width_mode and semantic_analyzer is not None
            else None
        )
        if width_mode:
            (
                protected_boundaries,
                linguistic_boundary_penalties,
                lexical_units,
            ) = _vietnamese_protected_word_boundaries(
                words, constituency_analysis=constituency_analysis
            )
        else:
            protected_boundaries = set()
            linguistic_boundary_penalties = {}
            lexical_units = []
        protected_lexical_units.extend(
            {"referenceGroup": group, **item} for item in lexical_units
        )
        def segment_text(left: int, right: int) -> str:
            return " ".join(str(item.get("text", "")) for item in words[left:right])

        def segment_width(left: int, right: int) -> float:
            if not width_mode or measure_text is None:
                return float(right - left)
            return float(measure_text(segment_text(left, right)))

        punctuation_boundaries = {
            index + 1
            for index, word in enumerate(words[:-1])
            if PUNCTUATION_BREAK.search(str(word.get("text", "")))
        }
        if width_mode:
            width_limit = float(maximum_line_width)
            hard_width_limit = float(hard_maximum_line_width or width_limit)
            authoritative_punctuation_boundaries = set(punctuation_boundaries)
            # User-supplied punctuation closes a real lyric clause. Parser
            # dependencies must never glue words across that boundary.
            protected_boundaries.difference_update(
                authoritative_punctuation_boundaries
            )
            for candidate in (
                constituency_analysis or {}
            ).get("punctuationBoundaries", []):
                boundary = int(candidate.get("boundary", 0))
                if not 0 < boundary < word_count or boundary in protected_boundaries:
                    continue
                probability = float(candidate.get("probability", 0.0))
                margin = float(candidate.get("margin", 0.0))
                previous = words[boundary - 1]
                following = words[boundary]
                acoustic_end = float(
                    previous.get("acousticEnd", previous["end"])
                )
                pause = max(0.0, float(following["start"]) - acoustic_end)
                syntax_penalty = float(
                    linguistic_boundary_penalties.get(boundary, 0.0)
                )
                high_confidence = (
                    probability >= 0.60
                    and margin >= 0.20
                    and syntax_penalty <= 40.0
                )
                consensus_confidence = (
                    probability >= 0.40
                    and margin >= 0.05
                    and pause >= 0.45
                    and syntax_penalty <= 40.0
                )
                surrounding = sorted(
                    {0, word_count, *authoritative_punctuation_boundaries}
                )
                left = max(item for item in surrounding if item < boundary)
                right = min(item for item in surrounding if item > boundary)
                non_orphan = min(
                    segment_width(left, boundary),
                    segment_width(boundary, right),
                ) >= 0.30 * width_limit
                if non_orphan and (high_confidence or consensus_confidence):
                    punctuation_boundaries.add(boundary)
                    inferred_semantic_boundaries.append(
                        {
                            "referenceGroup": group,
                            "afterText": str(previous.get("text", "")),
                            "mark": str(candidate.get("mark", "")),
                            "probability": round(probability, 4),
                            "keepMargin": round(margin, 4),
                            "pauseSeconds": round(pause, 4),
                            "syntaxPenalty": round(syntax_penalty, 3),
                            "evidence": (
                                "punctuation-model-high-confidence"
                                if high_confidence
                                else "punctuation-syntax-acoustic-consensus"
                            ),
                        }
                    )

            def partition_is_feasible(
                chunk_total: int,
                required_boundaries: set[int],
                candidate_width_limit: float,
            ) -> bool:
                reachable: set[tuple[int, int]] = {(0, 0)}
                for used_chunks in range(chunk_total):
                    for cursor in range(word_count + 1):
                        if (used_chunks, cursor) not in reachable:
                            continue
                        remaining_chunks = chunk_total - used_chunks - 1
                        for end in range(cursor + 1, word_count - remaining_chunks + 1):
                            if end < word_count and end in protected_boundaries:
                                continue
                            if any(
                                cursor < boundary < end
                                for boundary in required_boundaries
                            ):
                                continue
                            width = segment_width(cursor, end)
                            if (
                                chunk_total > 1
                                and width < 0.28 * width_limit
                            ):
                                continue
                            if width <= candidate_width_limit:
                                reachable.add((used_chunks + 1, end))
                return (chunk_total, word_count) in reachable

            minimum_chunk_count = next(
                (
                    count
                    for count in range(1, word_count + 1)
                    if partition_is_feasible(
                        count,
                        authoritative_punctuation_boundaries,
                        hard_width_limit,
                    )
                ),
                None,
            )
            if minimum_chunk_count is None:
                raise ValueError(
                    "Unable to fit protected semantic units in lyric phrase "
                    f"group {group}"
                )
            # Punctuation is strong semantic evidence, but it is not an
            # unconditional display boundary. Requiring every comma can add
            # a whole extra row or strand a vocative on a tiny row. The DP
            # below strongly rewards these boundaries whenever the minimum
            # readable row count can accommodate them.
            required_punctuation_boundaries = set(
                authoritative_punctuation_boundaries
            )
            chunk_count = minimum_chunk_count
            ideal_width = segment_width(0, word_count) / chunk_count
            ideal_size = None
            minimum_size = None
        else:
            required_punctuation_boundaries = set()
            assert maximum_words is not None and target_words is not None
            chunk_count = max(
                math.ceil(word_count / maximum_words),
                math.ceil(word_count / target_words),
            )
            chunk_count = min(chunk_count, word_count)
            ideal_size = word_count / chunk_count
            minimum_size = min(minimum_words, max(1, math.floor(ideal_size)))
        pause_weight = 4.0
        orphan_penalty = 200.0
        states: dict[tuple[int, int], tuple[float, list[int]]] = {(0, 0): (0.0, [])}
        for used_chunks in range(chunk_count):
            for cursor in range(word_count + 1):
                state = states.get((used_chunks, cursor))
                if state is None:
                    continue
                remaining_chunks = chunk_count - used_chunks - 1
                minimum_end = cursor + 1
                maximum_end = word_count - remaining_chunks
                if not width_mode:
                    assert maximum_words is not None
                    maximum_end = min(maximum_end, cursor + maximum_words)
                for end in range(minimum_end, maximum_end + 1):
                    if end < word_count and end in protected_boundaries:
                        continue
                    if any(
                        cursor < boundary < end
                        for boundary in required_punctuation_boundaries
                    ):
                        continue
                    remaining_words = word_count - end
                    if remaining_words < remaining_chunks:
                        continue
                    if (
                        not width_mode
                        and maximum_words is not None
                        and remaining_words > remaining_chunks * maximum_words
                    ):
                        continue
                    size = end - cursor
                    width = segment_width(cursor, end)
                    if width_mode and width > hard_width_limit:
                        continue
                    if width_mode:
                        balance = (width - ideal_width) / max(
                            float(maximum_line_width), 1.0
                        )
                        cost = state[0] + 4.0 * balance * balance
                        if width > width_limit:
                            shrink_ratio = (width - width_limit) / width_limit
                            cost += 5000.0 * shrink_ratio * shrink_ratio
                        if (
                            chunk_count > 1
                            and width < 0.28 * width_limit
                        ):
                            continue
                        if chunk_count > 1 and size == 1:
                            cost += orphan_penalty
                    else:
                        assert ideal_size is not None and minimum_size is not None
                        cost = state[0] + (size - ideal_size) ** 2
                        if size < minimum_size and word_count > minimum_words:
                            cost += orphan_penalty * (minimum_size - size)
                    if end < word_count:
                        previous = words[end - 1]
                        following = words[end]
                        cost += _vietnamese_line_break_semantic_penalty(
                            str(previous.get("text", "")),
                            str(following.get("text", "")),
                        )
                        cost += linguistic_boundary_penalties.get(end, 0.0)
                        acoustic_end = float(
                            previous.get("acousticEnd", previous["end"])
                        )
                        pause = max(0.0, float(following["start"]) - acoustic_end)
                        punctuation = end in punctuation_boundaries
                        if punctuation:
                            # A curated comma/colon closes a semantic phrase.
                            # Prefer it over a merely equal acoustic pause so
                            # vocatives such as "Còn gì đâu em," stay intact.
                            cost -= 180.0
                        else:
                            pause_ratio = min(
                                1.0,
                                pause / max(natural_pause_seconds, 1e-6),
                            )
                            cost += pause_weight * (1.0 - pause_ratio)
                            # Do not flatten every pause above the natural
                            # threshold into the same score. A held syllable
                            # followed by a long breath is stronger evidence
                            # of a sung phrase boundary than a short internal
                            # articulation gap, even when both exceed 180 ms.
                            long_pause_seconds = min(
                                2.0,
                                max(0.0, pause - natural_pause_seconds),
                            )
                            cost -= 1.5 * long_pause_seconds
                    key = (used_chunks + 1, end)
                    candidate_breaks = [*state[1], end]
                    current = states.get(key)
                    if current is None or cost < current[0]:
                        states[key] = (cost, candidate_breaks)
        final_state = states.get((chunk_count, word_count))
        if final_state is None:
            raise ValueError(f"Unable to reflow lyric phrase group {group}")
        cursor = 0
        for end in final_state[1]:
            segment = words[cursor:end]
            base = next(
                (
                    line
                    for index, line in enumerate(lines, start=1)
                    if int(line.get("referenceGroup", index)) == group
                ),
                {},
            )
            item = dict(base)
            item["referenceGroup"] = group
            item["start"] = float(segment[0]["start"])
            item["end"] = float(segment[-1]["end"])
            item["text"] = " ".join(str(word["text"]) for word in segment)
            item["syllables"] = segment
            output.append(item)
            if (
                not width_mode
                and len(segment) < minimum_words
                and word_count > minimum_words
            ):
                orphan_line_count += 1
            if end < word_count:
                acoustic_end = float(
                    segment[-1].get("acousticEnd", segment[-1]["end"])
                )
                chosen_breaks.append(
                    {
                        "referenceGroup": group,
                        "afterText": str(segment[-1]["text"]),
                        "pauseSeconds": round(
                            max(0.0, float(words[end]["start"]) - acoustic_end),
                            4,
                        ),
                        "leftWords": len(segment),
                    }
                )
            cursor = end
    for index, line in enumerate(output, start=1):
        line["index"] = index
        line["slot"] = "top" if index % 2 else "bottom"
    reflowed_word_stream = [
        str(syllable.get("text", ""))
        for line in output
        for syllable in line.get("syllables", [])
    ]
    if reflowed_word_stream != authoritative_word_stream:
        raise ValueError("Lyric reflow changed the authoritative word stream")
    return output, {
        "policy": (
            "semantic-pause-visual-width-reflow"
            if width_mode
            else "pause-aware-balanced-semantic-phrase-reflow"
        ),
        "hardWordLimitUsed": not width_mode,
        "maximumLineWidth": (
            round(float(maximum_line_width), 3) if width_mode else None
        ),
        "hardMaximumLineWidth": (
            round(float(hard_maximum_line_width), 3)
            if width_mode and hard_maximum_line_width is not None
            else None
        ),
        "lineCountBefore": len(lines),
        "lineCountAfter": len(output),
        "orphanLineCount": orphan_line_count,
        "authoritativeWordStreamPreserved": True,
        "semanticEvidence": (
            LIGHTWEIGHT_REFLOW_EVIDENCE
            if width_mode and semantic_analyzer is None
            else (
                "explicit-test-analyzer"
                if width_mode
                else "authoritative-punctuation+aligned-acoustic-pauses+curated-break-penalties"
            )
        ),
        "chosenBreaks": chosen_breaks,
        "inferredSemanticBoundaries": inferred_semantic_boundaries,
        "protectedLexicalUnits": protected_lexical_units,
        "protectedLexicalBoundaryCount": sum(
            max(0, int(item["endWord"]) - int(item["startWord"]))
            for item in protected_lexical_units
        ),
    }


def karaoke_timing_qc(
    lines: list[dict[str, Any]],
    *,
    maximum_words: int | None,
    maximum_screen_words: int | None = None,
    maximum_line_duration: float,
    alignment_diagnostics: dict[str, Any] | None = None,
    forced_alignment_diagnostics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a machine-readable render gate for lyric timing and segmentation."""
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    previous_start = -1.0
    overlap_count = 0
    maximum_observed_words = 0
    maximum_observed_screen_words = 0
    maximum_observed_duration = 0.0
    previous_word_count = 0
    ordered_syllables: list[dict[str, Any]] = []
    for line_position, line in enumerate(lines):
        line_number = int(line.get("index", 0))
        syllables = list(line.get("syllables", []))
        word_count = len(syllables)
        duration = float(line["end"]) - float(line["start"])
        maximum_observed_words = max(maximum_observed_words, word_count)
        screen_word_count = word_count + (previous_word_count if line_position else 0)
        maximum_observed_screen_words = max(
            maximum_observed_screen_words, screen_word_count
        )
        maximum_observed_duration = max(maximum_observed_duration, duration)
        if maximum_words is not None and word_count > maximum_words:
            errors.append(
                {
                    "code": "PHRASE_TOO_LONG",
                    "line": line_number,
                    "observed": word_count,
                    "maximum": maximum_words,
                }
            )
        if maximum_screen_words is not None and screen_word_count > maximum_screen_words:
            errors.append(
                {
                    "code": "SCREEN_TOO_LONG",
                    "lines": [
                        int(lines[line_position - 1].get("index", line_position))
                        if line_position
                        else line_number,
                        line_number,
                    ],
                    "observed": screen_word_count,
                    "maximum": maximum_screen_words,
                }
            )
        if duration > maximum_line_duration:
            warnings.append(
                {
                    "code": "PHRASE_DURATION_LONG",
                    "line": line_number,
                    "observedSeconds": round(duration, 3),
                    "maximumSeconds": maximum_line_duration,
                }
            )
        if float(line["start"]) < previous_start:
            errors.append({"code": "NON_MONOTONIC_LINE", "line": line_number})
        previous_start = float(line["start"])
        previous_word_count = word_count
        ordered_syllables.extend(syllables)
    for current, following in zip(ordered_syllables, ordered_syllables[1:]):
        if float(current["end"]) > float(following["start"]) + 0.001:
            overlap_count += 1
    if overlap_count:
        warnings.append(
            {"code": "OVERLAPPING_SYLLABLES", "observed": overlap_count}
        )

    alignment_coverage: float | None = None
    if alignment_diagnostics:
        reference_count = int(alignment_diagnostics.get("referenceWordCount", 0))
        mapped_count = int(alignment_diagnostics.get("mappedWordCount", 0))
        if reference_count:
            alignment_coverage = mapped_count / reference_count
            if alignment_coverage < 0.9:
                errors.append(
                    {
                        "code": "ALIGNMENT_COVERAGE_LOW",
                        "observed": round(alignment_coverage, 4),
                        "minimum": 0.9,
                    }
                )
    if forced_alignment_diagnostics:
        forced_word_count = int(forced_alignment_diagnostics.get("wordCount", 0))
        observed_word_count = sum(len(line.get("syllables", [])) for line in lines)
        if forced_word_count != observed_word_count:
            errors.append(
                {
                    "code": "FORCED_ALIGNMENT_INCOMPLETE",
                    "observed": forced_word_count,
                    "expected": observed_word_count,
                }
            )
        unstamped_word_count = sum(
            not str(word.get("alignmentSource", "")).startswith(
                "vietnamese-song-ctc"
            )
            for line in lines
            for word in line.get("syllables", [])
        )
        if unstamped_word_count:
            errors.append(
                {
                    "code": "FORCED_ALIGNMENT_SOURCE_MISSING",
                    "observed": unstamped_word_count,
                    "maximum": 0,
                }
            )
        low_confidence_count = int(
            forced_alignment_diagnostics.get("lowConfidenceWordCount", 0)
        )
        if low_confidence_count:
            issue = {
                "code": "FORCED_ALIGNMENT_CONFIDENCE_LOW",
                "observed": low_confidence_count,
                "maximum": 0,
            }
            if forced_alignment_diagnostics.get("confidencePolicy") == "best-score":
                warnings.append(issue)
            else:
                errors.append(issue)
    return {
        "status": "failed" if errors else "passed-with-warnings" if warnings else "passed",
        "errors": errors,
        "warnings": warnings,
        "metrics": {
            "lineCount": len(lines),
            "maximumWordsPerPhrase": maximum_observed_words,
            "maximumWordsPerLine": maximum_observed_words,
            "maximumWordsPerScreen": maximum_observed_screen_words,
            "maximumLineDurationSeconds": round(maximum_observed_duration, 3),
            "syllableOverlapCount": overlap_count,
            "alignmentCoverage": (
                round(alignment_coverage, 4) if alignment_coverage is not None else None
            ),
            "forcedAlignedWordCount": (
                int(forced_alignment_diagnostics.get("wordCount", 0))
                if forced_alignment_diagnostics
                else 0
            ),
            "minimumWordAlignmentConfidence": (
                forced_alignment_diagnostics.get("minimumConfidence")
                if forced_alignment_diagnostics
                else None
            ),
        },
    }


def _align_authoritative_lyrics(context: StageContext) -> list[dict[str, Any]]:
    """Force-align exact user text to the vocal stem without text recognition."""
    root = _project_root(context)
    config = load_project_config(root)["pipeline"].get("lyrics", {})
    source = load_json(_authoritative_lyrics_file(context))
    if source.get("mode") != "authoritative-input":
        raise ValueError("Authoritative lyric input is required for alignment.")
    reference_lines = [
        str(item.get("text", ""))
        for item in source.get("lines", [])
        if str(item.get("text", "")).strip()
    ]
    if not reference_lines:
        raise ValueError("Authoritative lyric input contains no lines.")

    lines: list[dict[str, Any]] = []
    for reference_group, text in enumerate(reference_lines, start=1):
        syllables = [
            {
                "text": token,
                "start": 0.0,
                "end": 0.01,
                "timingGuideMatch": "authoritative-input",
            }
            for token in text.split()
            if token
        ]
        if not syllables:
            continue
        lines.append(
            {
                "index": len(lines) + 1,
                "slot": "top" if len(lines) % 2 == 0 else "bottom",
                "start": 0.0,
                "end": 0.01,
                "text": " ".join(item["text"] for item in syllables),
                "role": None,
                "referenceGroup": reference_group,
                "syllables": syllables,
            }
        )
    reference_word_count = sum(len(line["syllables"]) for line in lines)
    input_diagnostics = {
        "referenceWordCount": reference_word_count,
        "mappedWordCount": reference_word_count,
        "alignmentMode": "authoritative-text-full-vocal",
        "speechAsrTimingUsed": False,
        "detectedTextUsed": False,
        "captionUsed": False,
        "inputSha256": source.get("sha256"),
    }

    engine = str(config.get("forcedAlignmentEngine", "")).strip()
    if not bool(config.get("forcedAlignment", True)) or engine != "vietnamese-song-ctc":
        raise ValueError(
            "Authoritative lyrics require the vietnamese-song-ctc forced-alignment engine."
        )
    context.progress(10, "Loading Vietnamese song forced aligner")
    lines, forced_alignment_diagnostics = force_align_full_song_lines(
        root,
        _vocals(context),
        lines,
        config,
        source_audio_path=_source_audio(context),
        enforce_minimum_confidence=True,
        progress=lambda current, total: context.progress(
            10 + 70 * current / total,
            f"Forced-aligned audio block {current}/{total}",
        ),
    )
    forced_alignment_diagnostics["textSource"] = "authoritative-user-input"
    forced_alignment_diagnostics["detectedTextUsed"] = False
    forced_alignment_diagnostics["captionUsed"] = False
    context.log(
        "Authoritative lyric forced alignment: "
        + json.dumps(forced_alignment_diagnostics, ensure_ascii=False)
    )

    if bool(config.get("pauseAwareLineWrapping", True)):
        try:
            from PIL import ImageFont
        except ImportError as exc:
            raise RuntimeError(
                "Semantic lyric layout requires Pillow for glyph measurement."
            ) from exc
        pipeline = load_project_config(root)["pipeline"]
        template = load_json(root / str(pipeline.get("render", {}).get("template")))
        play_x = int(template.get("referenceResolution", [1920, 1080])[0])
        layout = template.get("layout", {})
        font_settings = template.get("font", {})
        safe_area = float(layout.get("safeAreaPercent", 5.0))
        base_font_size = int(font_settings.get("sizeAt1080p", 128))
        (
            auto_shrink_long_lines,
            minimum_font_size,
            preferred_minimum_font_size,
        ) = lyric_font_size_policy(layout, base_font_size)
        font_path = root / "assets" / "fonts" / str(font_settings.get("file", ""))
        font = ImageFont.truetype(str(font_path), base_font_size)
        scale_x = float(font_settings.get("scaleX", 100.0)) / 100.0
        outline_allowance = 2.0 * (
            float(template.get("unsung", {}).get("outerOutlineWidth", 0.0))
            + float(template.get("sung", {}).get("innerOutlineWidth", 0.0))
            + float(template.get("unsung", {}).get("shadowOffset", 0.0))
        )
        available_render_width = (
            play_x
            * min(
                1.0 - 2.0 * safe_area / 100.0,
                float(layout.get("maximumLineWidthPercent", 90.0)) / 100.0,
            )
            - outline_allowance
        )
        semantic_reflow_width = (
            available_render_width
            * base_font_size
            / preferred_minimum_font_size
        )
        hard_reflow_width = (
            available_render_width * base_font_size / minimum_font_size
        )
        measure_text = lambda text: float(font.getlength(text)) * scale_x
        lines, line_reflow = reflow_aligned_lyric_lines(
            lines,
            maximum_words=None,
            target_words=None,
            minimum_words=1,
            natural_pause_seconds=float(
                config.get("naturalLineBreakPauseSeconds", 0.18)
            ),
            measure_text=measure_text,
            maximum_line_width=semantic_reflow_width,
            hard_maximum_line_width=hard_reflow_width,
        )
        line_reflow["generatedPunctuation"] = False
        fitted_sizes: list[int] = []
        for line in lines:
            width = measure_text(str(line["text"]))
            fitted_size = (
                min(
                    base_font_size,
                    max(
                        minimum_font_size,
                        int(
                            math.floor(
                                base_font_size
                                * available_render_width
                                / max(width, 1.0)
                            )
                        ),
                    ),
                )
                if auto_shrink_long_lines
                else base_font_size
            )
            line["fontSizeAt1080p"] = fitted_size
            line["measuredWidthAtBaseFont"] = round(width, 3)
            fitted_sizes.append(fitted_size)
        line_reflow["availableRenderWidth"] = round(available_render_width, 3)
        line_reflow["autoShrinkLongLines"] = auto_shrink_long_lines
        line_reflow["preferredMinimumFontSize"] = preferred_minimum_font_size
        line_reflow["minimumFittedFontSize"] = min(fitted_sizes)
        forced_alignment_diagnostics["lineReflow"] = line_reflow
        context.log(
            "Pause-aware lyric reflow: "
            + json.dumps(line_reflow, ensure_ascii=False)
        )

    quality_control = karaoke_timing_qc(
        lines,
        maximum_words=None,
        maximum_screen_words=None,
        maximum_line_duration=float(config.get("maxLineDurationSeconds", 11.0)),
        alignment_diagnostics=input_diagnostics,
        forced_alignment_diagnostics=forced_alignment_diagnostics,
    )
    if quality_control["errors"]:
        raise ValueError(
            "Authoritative lyric timing failed production quality control: "
            + json.dumps(quality_control["errors"], ensure_ascii=False)
        )
    context.log(f"Lyric timing quality control: {quality_control}")

    leakage_settings = config.get("leakageQualityGate", {})
    context.progress(85, "Checking lyric-shaped vocal bleed in the instrumental")
    leakage_quality = refine_lyric_leakage(
        _instrumental(context),
        _vocals(context),
        lines,
        leakage_settings,
        project_root=root,
        alignment_settings=config,
    )
    atomic_write_json(_lyric_leakage_qc_file(context), leakage_quality)
    context.log(
        "Lyric leakage quality control: "
        + json.dumps(leakage_quality, ensure_ascii=False)
    )
    if leakage_quality["errors"]:
        raise ValueError(
            "Instrumental lyric leakage failed production quality control: "
            + json.dumps(leakage_quality["errors"], ensure_ascii=False)
        )
    if leakage_quality.get("metrics", {}).get("refinementApplied"):
        separation_config = load_project_config(root)["pipeline"].get(
            "audioSeparation", {}
        )
        post_refinement_stem_quality = stem_separation_qc(
            _source_audio(context),
            _instrumental(context),
            _vocals(context),
            separation_config.get("qualityGates", {}),
        )
        if post_refinement_stem_quality["errors"]:
            raise ValueError(
                "Post-refinement stems failed production quality control: "
                + json.dumps(post_refinement_stem_quality["errors"])
            )
        separation_quality = load_json(_stem_qc_file(context))
        separation_quality["postLyricLeakageRefinement"] = post_refinement_stem_quality
        atomic_write_json(_stem_qc_file(context), separation_quality)

    payload = {
        "schemaVersion": 1,
        "language": str(config.get("language", "vi")),
        "timingUnit": "word-as-vietnamese-syllable",
        "lineCount": len(lines),
        "alignment": "authoritative-text-forced-alignment",
        "textSource": "authoritative-user-input",
        "authoritativeLyrics": {
            "sha256": source.get("sha256"),
            "sourcePath": source.get("sourcePath"),
            "snapshot": source.get("snapshot"),
            "lineCount": source.get("lineCount"),
            "wordCount": source.get("wordCount"),
        },
        "detectedTextUsed": False,
        "captionUsed": False,
        "alignmentDiagnostics": input_diagnostics,
        "forcedAlignment": forced_alignment_diagnostics,
        "qualityControl": quality_control,
        "timingSource": "vietnamese-song-ctc-forced-alignment",
        "lines": lines,
    }
    output = _lyrics(context)
    atomic_write_json(_aligned_lyrics(context), payload)
    atomic_write_json(output, payload)
    context.progress(100, f"Built {len(lines)} karaoke lines from exact input text")
    return [
        _artifact(output, "lyrics", "Aligned authoritative karaoke lyrics"),
        _artifact(
            _lyric_leakage_qc_file(context),
            "analysis",
            "Lyric leakage quality control",
        ),
    ]


def _resolve_short_semantic_group_roles(
    roles: list[str],
    pitch_medians: list[float | None],
    margins: list[float],
    reference_groups: list[int],
    settings: dict[str, Any],
) -> list[dict[str, Any]]:
    """Break a two-line speaker tie with independent absolute-pitch evidence."""
    if not (
        len(roles) == len(pitch_medians) == len(margins) == len(reference_groups)
    ):
        raise ValueError("Short semantic-group speaker evidence must have equal lengths")
    male_maximum = float(settings.get("maleMaximumMedianHz", 235.0))
    female_minimum = float(settings.get("femaleMinimumMedianHz", 275.0))
    maximum_ambiguous_margin = float(
        settings.get("maximumSpeakerAmbiguousPitchResolutionMargin", 0.05)
    )

    def pitch_role(value: float | None) -> str | None:
        if value is None or not math.isfinite(float(value)):
            return None
        if float(value) <= male_maximum:
            return "male"
        if float(value) >= female_minimum:
            return "female"
        return None

    by_group: dict[int, list[int]] = {}
    for index, group in enumerate(reference_groups):
        by_group.setdefault(int(group), []).append(index)
    resolutions: list[dict[str, Any]] = []
    for group, indexes in by_group.items():
        if len(indexes) != 2 or len({roles[index] for index in indexes}) != 2:
            continue
        votes = [pitch_role(pitch_medians[index]) for index in indexes]
        definitive = [vote for vote in votes if vote is not None]
        winner: str | None = None
        evidence = ""
        if len(definitive) == 2 and definitive[0] == definitive[1]:
            winner = definitive[0]
            evidence = "two-consistent-pitch-votes"
        elif len(definitive) == 1:
            ambiguous_position = votes.index(None)
            ambiguous_index = indexes[ambiguous_position]
            if float(margins[ambiguous_index]) <= maximum_ambiguous_margin:
                winner = definitive[0]
                evidence = "pitch-vote-plus-weak-ambiguous-speaker"
        if winner is None:
            continue
        changed = [index for index in indexes if roles[index] != winner]
        for index in changed:
            roles[index] = winner
        if changed:
            resolutions.append(
                {
                    "referenceGroup": group,
                    "role": winner,
                    "changedLineIndexes": [index + 1 for index in changed],
                    "pitchVotes": votes,
                    "evidence": evidence,
                }
            )
    return resolutions


def cluster_speaker_embeddings(
    embeddings: Any,
    pitch_medians: list[float | None],
    reference_groups: list[int],
    settings: dict[str, Any],
) -> tuple[list[str], dict[str, Any], dict[str, Any]]:
    """Infer one or two lead singers without forcing a binary split.

    Timbre decides whether a second identity exists. Absolute pitch maps each
    accepted identity to a display role. Weak evidence is never converted into
    a guessed male/female assignment.
    """
    import numpy as np

    matrix = np.asarray(embeddings, dtype=np.float32)
    if matrix.ndim != 2 or matrix.shape[0] < 2:
        raise ValueError("Speaker inference requires at least two lyric embeddings")
    if len(pitch_medians) != len(matrix) or len(reference_groups) != len(matrix):
        raise ValueError("Speaker evidence must match the lyric line count")
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    matrix = matrix / np.maximum(norms, 1e-9)

    pairwise = matrix @ matrix.T
    first, second = np.unravel_index(int(np.argmin(pairwise)), pairwise.shape)
    if first == second:
        first, second = 0, 1
    candidate_centers = np.stack((matrix[first], matrix[second]))
    candidate_assignments = np.zeros(len(matrix), dtype=np.int64)
    for _ in range(50):
        similarity = matrix @ candidate_centers.T
        updated = np.argmax(similarity, axis=1)
        if np.array_equal(updated, candidate_assignments) and _ > 0:
            break
        candidate_assignments = updated
        for cluster in (0, 1):
            members = matrix[candidate_assignments == cluster]
            if not len(members):
                candidate_assignments = np.zeros(len(matrix), dtype=np.int64)
                break
            center = np.mean(members, axis=0)
            candidate_centers[cluster] = center / max(
                float(np.linalg.norm(center)), 1e-9
            )

    candidate_similarity = matrix @ candidate_centers.T
    candidate_own = candidate_similarity[
        np.arange(len(matrix)), candidate_assignments
    ]
    candidate_other = candidate_similarity[
        np.arange(len(matrix)), 1 - candidate_assignments
    ]
    candidate_margins = candidate_own - candidate_other
    candidate_mean_margin = float(np.mean(candidate_margins))
    center_distance = float(1.0 - np.dot(candidate_centers[0], candidate_centers[1]))
    minimum_margin = float(settings.get("minimumSpeakerClusterMeanMargin", 0.1))
    minimum_center_distance = float(
        settings.get("minimumSpeakerCenterCosineDistance", 0.2)
    )
    ambiguity_center_distance = float(
        settings.get("speakerAmbiguityMinimumCenterCosineDistance", 0.1)
    )
    minimum_cluster_lines = int(settings.get("minimumSingerClusterLines", 2))
    minimum_cluster_ratio = float(settings.get("minimumSingerClusterRatio", 0.12))
    minimum_cluster_groups = int(
        settings.get("minimumSingerClusterSemanticGroups", 1)
    )
    cluster_sizes = {
        cluster: int(np.count_nonzero(candidate_assignments == cluster))
        for cluster in (0, 1)
    }
    cluster_group_counts = {
        cluster: len(
            {
                int(reference_groups[index])
                for index in range(len(matrix))
                if int(candidate_assignments[index]) == cluster
            }
        )
        for cluster in (0, 1)
    }
    population_gate_passed = all(
        cluster_sizes[cluster] >= minimum_cluster_lines
        and cluster_sizes[cluster] / len(matrix) >= minimum_cluster_ratio
        and cluster_group_counts[cluster] >= minimum_cluster_groups
        for cluster in (0, 1)
    )
    margin_gate_passed = candidate_mean_margin >= minimum_margin
    center_gate_passed = center_distance >= minimum_center_distance
    two_singer_evidence = bool(
        settings.get("adaptiveSpeakerCount", True)
        and population_gate_passed
        and margin_gate_passed
        and center_gate_passed
    )
    ambiguous_two_singer_evidence = bool(
        all(cluster_sizes[cluster] > 0 for cluster in (0, 1))
        and center_distance >= ambiguity_center_distance
        and not two_singer_evidence
    )
    if ambiguous_two_singer_evidence and bool(
        settings.get("failOnAmbiguousSingerCount", True)
    ):
        raise ValueError(
            "Singer-count evidence is ambiguous; refusing to force one or two singers "
            f"(centerDistance={center_distance:.4f}, "
            f"meanMargin={candidate_mean_margin:.4f})"
        )

    if two_singer_evidence:
        centers = candidate_centers
        assignments = candidate_assignments
        margins = candidate_margins
        cluster_count = 2
    else:
        center = np.mean(matrix, axis=0)
        center = center / max(float(np.linalg.norm(center)), 1e-9)
        centers = np.expand_dims(center, axis=0)
        assignments = np.zeros(len(matrix), dtype=np.int64)
        similarity = matrix @ centers.T
        margins = similarity[:, 0]
        cluster_count = 1

    cluster_pitches: dict[int, float] = {}
    for cluster in range(cluster_count):
        values = [
            float(pitch)
            for pitch, assignment in zip(pitch_medians, assignments)
            if assignment == cluster and pitch is not None and math.isfinite(float(pitch))
        ]
        if not values:
            raise ValueError(f"Speaker cluster {cluster} has no reliable pitch evidence")
        cluster_pitches[cluster] = float(np.median(values))

    male_maximum = float(settings.get("maleMaximumMedianHz", 235.0))
    female_minimum = float(settings.get("femaleMinimumMedianHz", 275.0))

    def absolute_role(pitch: float) -> str | None:
        if pitch <= male_maximum:
            return "male"
        if pitch >= female_minimum:
            return "female"
        return None

    role_by_cluster: dict[int, str] = {}
    ambiguous_gender_clusters: list[int] = []
    for cluster, pitch in cluster_pitches.items():
        role = absolute_role(pitch)
        if role is None:
            ambiguous_gender_clusters.append(cluster)
        else:
            role_by_cluster[cluster] = role
    if ambiguous_gender_clusters and bool(settings.get("failOnAmbiguousGender", True)):
        details = {cluster: round(cluster_pitches[cluster], 2) for cluster in ambiguous_gender_clusters}
        raise ValueError(
            "Absolute pitch cannot establish a male/female display role; "
            f"ambiguous clusters={details}, maleMaximum={male_maximum}, "
            f"femaleMinimum={female_minimum}"
        )
    fallback_role = str(settings.get("defaultLeadRole", "male")).lower()
    if fallback_role not in {"male", "female"}:
        fallback_role = "male"
    for cluster in ambiguous_gender_clusters:
        role_by_cluster[cluster] = fallback_role

    ordered_pitches = sorted(cluster_pitches.values())
    pitch_ratio = (
        ordered_pitches[-1] / max(ordered_pitches[0], 1e-9)
        if cluster_count == 2
        else 1.0
    )
    minimum_pitch_ratio = float(settings.get("minimumSpeakerPitchRatio", 1.2))
    pitch_gate_passed = cluster_count == 1 or pitch_ratio >= minimum_pitch_ratio
    separation_score = min(
        1.0,
        max(0.0, candidate_mean_margin / max(minimum_margin, 1e-9)),
    )
    center_score = min(
        1.0, max(0.0, center_distance / max(minimum_center_distance, 1e-9))
    )
    evidence_score = 100.0 * (0.55 * separation_score + 0.45 * center_score)
    roles = [role_by_cluster[int(cluster)] for cluster in assignments]
    raw_roles = list(roles)

    majority_ratio = float(settings.get("speakerPhraseMajorityRatio", 0.66))
    by_group: dict[int, list[int]] = {}
    for index, group in enumerate(reference_groups):
        by_group.setdefault(int(group), []).append(index)
    smoothed: list[dict[str, Any]] = []
    for group, indexes in by_group.items():
        if len(indexes) < 3:
            continue
        counts = {role: sum(roles[index] == role for index in indexes) for role in ("male", "female")}
        winner = max(counts, key=counts.get)
        if counts[winner] / len(indexes) < majority_ratio:
            continue
        changed = [index for index in indexes if roles[index] != winner]
        for index in changed:
            roles[index] = winner
        if changed:
            smoothed.append(
                {
                    "referenceGroup": group,
                    "role": winner,
                    "changedLineIndexes": [index + 1 for index in changed],
                }
            )
    short_phrase_resolutions = _resolve_short_semantic_group_roles(
        roles,
        pitch_medians,
        [float(value) for value in margins],
        reference_groups,
        settings,
    )

    report = {
        "clusterCount": cluster_count,
        "speakerCountDecision": {
            "status": "passed",
            "selected": cluster_count,
            "policy": "adaptive-timbre-with-fail-closed-ambiguity",
            "candidateTwoSinger": {
                "centerCosineDistance": round(center_distance, 4),
                "meanCosineMargin": round(candidate_mean_margin, 4),
                "clusterSizes": {str(key): value for key, value in cluster_sizes.items()},
                "semanticGroupCounts": {
                    str(key): value for key, value in cluster_group_counts.items()
                },
                "populationGatePassed": population_gate_passed,
                "centerGatePassed": center_gate_passed,
                "marginGatePassed": margin_gate_passed,
            },
        },
        "clusterSizes": {
            str(cluster): int(np.count_nonzero(assignments == cluster))
            for cluster in range(cluster_count)
        },
        "clusterMedianPitchHz": {
            str(cluster): round(cluster_pitches[cluster], 2)
            for cluster in range(cluster_count)
        },
        "clusterRole": {
            str(cluster): role_by_cluster[cluster] for cluster in range(cluster_count)
        },
        "meanCosineMargin": round(float(np.mean(margins)), 4),
        "minimumCosineMargin": round(float(np.min(margins)), 4),
        "pitchRatio": round(pitch_ratio, 4),
        "evidenceSelection": {
            "status": "passed",
            "selectionPolicy": "strict-adaptive-speaker-and-absolute-pitch-evidence",
            "score": round(evidence_score, 3),
            "scoreScale": 100,
            "components": {
                "clusterSeparation": round(separation_score, 4),
                "centerSeparation": round(center_score, 4),
                "pitchRatioTargetMet": pitch_gate_passed,
            },
            "warnings": [],
        },
        "phraseMajoritySmoothing": smoothed,
        "shortPhrasePitchResolution": short_phrase_resolutions,
        "lines": [
            {
                "line": index + 1,
                "cluster": int(assignments[index]),
                "role": roles[index],
                "rawRole": raw_roles[index],
                "cosineMargin": round(float(margins[index]), 4),
                "medianPitchHz": (
                    round(float(pitch_medians[index]), 2)
                    if pitch_medians[index] is not None
                    else None
                ),
            }
            for index in range(len(matrix))
        ],
    }
    state = {
        "centers": centers,
        "roleByCluster": role_by_cluster,
    }
    return roles, report, state


def assign_speaker_embeddings(
    embeddings: Any,
    cluster_state: dict[str, Any],
    settings: dict[str, Any],
) -> tuple[list[str | None], list[dict[str, Any]]]:
    """Assign secondary vocal embeddings to the lead speaker clusters."""
    import numpy as np

    matrix = np.asarray(embeddings, dtype=np.float32)
    matrix = matrix / np.maximum(np.linalg.norm(matrix, axis=1, keepdims=True), 1e-9)
    centers = np.asarray(cluster_state["centers"], dtype=np.float32)
    similarity = matrix @ centers.T
    assignments = np.argmax(similarity, axis=1)
    minimum_margin = float(settings.get("minimumBackingSpeakerMargin", 0.0))
    minimum_solo_similarity = float(settings.get("minimumSoloSpeakerSimilarity", 0.55))
    roles: list[str | None] = []
    diagnostics: list[dict[str, Any]] = []
    for index, cluster in enumerate(assignments):
        if centers.shape[0] == 1:
            margin = float(similarity[index, 0])
            accepted = margin >= minimum_solo_similarity
        else:
            margin = float(similarity[index, cluster] - similarity[index, 1 - cluster])
            accepted = margin >= minimum_margin
        role = str(cluster_state["roleByCluster"][int(cluster)]) if accepted else None
        roles.append(role)
        diagnostics.append(
            {
                "line": index + 1,
                "cluster": int(cluster),
                "role": role,
                "cosineMargin": round(margin, 4),
            }
        )
    return roles, diagnostics


def decide_colead_roles(
    lead_roles: list[str],
    backing_roles: list[str | None],
    lexical_evidence: list[dict[str, Any]],
    settings: dict[str, Any],
    *,
    reference_groups: list[int] | None = None,
) -> tuple[list[str], dict[str, Any]]:
    """Promote only same-lyric, distinct-speaker evidence to duet."""
    if not (
        len(lead_roles) == len(backing_roles) == len(lexical_evidence)
    ):
        raise ValueError("Co-lead evidence must match the lyric line count")
    if reference_groups is None:
        reference_groups = list(range(1, len(lead_roles) + 1))
    if len(reference_groups) != len(lead_roles):
        raise ValueError("Co-lead phrase groups must match the lyric line count")
    minimum_coverage = float(settings.get("minimumCoLeadLexicalCoverage", 0.75))
    maximum_onset_delta = float(settings.get("maximumCoLeadOnsetDeltaSeconds", 0.65))
    minimum_ratio = float(settings.get("minimumCoLeadConfidenceRatio", 0.35))
    minimum_lead_confidence = float(settings.get("minimumCoLeadLeadConfidence", 0.5))
    minimum_consonant_count = int(settings.get("minimumCoLeadConsonantCount", 2))
    minimum_consonant_coverage = float(
        settings.get("minimumCoLeadConsonantCoverage", 0.6)
    )
    minimum_consonant_confidence = float(
        settings.get("minimumCoLeadMeanConsonantConfidence", 0.2)
    )
    minimum_consonant_ratio = float(
        settings.get("minimumCoLeadConsonantConfidenceRatio", 0.35)
    )

    def has_consonant_evidence(evidence: dict[str, Any]) -> bool:
        consonant_count = int(evidence.get("consonantCount", 0))
        return (
            consonant_count >= minimum_consonant_count
            and int(evidence.get("supportedConsonantCount", 0)) / consonant_count
            >= minimum_consonant_coverage
            and float(evidence.get("meanBackingConsonantConfidence", 0.0))
            >= minimum_consonant_confidence
            and float(evidence.get("backingToLeadConsonantConfidenceRatio", 0.0))
            >= minimum_consonant_ratio
        )

    def has_distinct_speakers(
        lead_role: str, backing_role: str | None, evidence: dict[str, Any]
    ) -> bool:
        if lead_role not in {"male", "female"} or backing_role not in {
            "male",
            "female",
        }:
            return False
        lead_cluster = evidence.get("leadSpeakerCluster")
        backing_cluster = evidence.get("backingSpeakerCluster")
        if isinstance(lead_cluster, int) and isinstance(backing_cluster, int):
            return lead_cluster != backing_cluster
        return backing_role != lead_role

    strong: list[bool] = []
    for lead_role, backing_role, evidence in zip(
        lead_roles, backing_roles, lexical_evidence
    ):
        word_count = max(1, int(evidence.get("wordCount", 0)))
        coverage = int(evidence.get("matchedWordCount", 0)) / word_count
        strong.append(
            has_distinct_speakers(lead_role, backing_role, evidence)
            and coverage >= minimum_coverage
            and float(evidence.get("meanOnsetDeltaSeconds", math.inf))
            <= maximum_onset_delta
            and float(evidence.get("backingToLeadConfidenceRatio", 0.0))
            >= minimum_ratio
            and float(evidence.get("meanLeadConfidence", 0.0))
            >= minimum_lead_confidence
            and has_consonant_evidence(evidence)
        )

    bridge_coverage = float(settings.get("minimumCoLeadBridgeCoverage", 0.75))
    bridge_delta = float(settings.get("maximumCoLeadBridgeOnsetDeltaSeconds", 0.35))
    bridge_ratio = float(settings.get("minimumCoLeadBridgeConfidenceRatio", 0.5))
    bridges = [False] * len(strong)
    for index in range(1, len(strong) - 1):
        if strong[index] or not (strong[index - 1] and strong[index + 1]):
            continue
        evidence = lexical_evidence[index]
        word_count = max(1, int(evidence.get("wordCount", 0)))
        coverage = int(evidence.get("matchedWordCount", 0)) / word_count
        bridges[index] = (
            coverage >= bridge_coverage
            and float(evidence.get("meanOnsetDeltaSeconds", math.inf)) <= bridge_delta
            and float(evidence.get("backingToLeadConfidenceRatio", 0.0)) >= bridge_ratio
            and float(evidence.get("meanLeadConfidence", 0.0)) >= minimum_lead_confidence
            and has_consonant_evidence(evidence)
        )

    phrase_continuations = [False] * len(strong)
    by_group: dict[int, list[int]] = {}
    for index, group in enumerate(reference_groups):
        by_group.setdefault(int(group), []).append(index)
    for indexes in by_group.values():
        strong_count = sum(strong[index] for index in indexes)
        if strong_count < 2:
            continue
        for index in indexes:
            if strong[index] or bridges[index]:
                continue
            evidence = lexical_evidence[index]
            word_count = max(1, int(evidence.get("wordCount", 0)))
            coverage = int(evidence.get("matchedWordCount", 0)) / word_count
            phrase_continuations[index] = (
                coverage >= bridge_coverage
                and float(evidence.get("meanOnsetDeltaSeconds", math.inf)) <= bridge_delta
                and float(evidence.get("backingToLeadConfidenceRatio", 0.0)) >= bridge_ratio
                and float(evidence.get("meanLeadConfidence", 0.0))
                >= minimum_lead_confidence
                and has_consonant_evidence(evidence)
            )

    roles = [
        "duet" if is_strong or is_bridge or is_continuation else lead_role
        for lead_role, is_strong, is_bridge, is_continuation in zip(
            lead_roles, strong, bridges, phrase_continuations
        )
    ]
    return roles, {
        "strongCoLeadLines": [index + 1 for index, value in enumerate(strong) if value],
        "bridgedCoLeadLines": [index + 1 for index, value in enumerate(bridges) if value],
        "phraseContinuationCoLeadLines": [
            index + 1 for index, value in enumerate(phrase_continuations) if value
        ],
        "duetLineCount": sum(role == "duet" for role in roles),
    }


def _line_pitch_medians(
    vocal_path: Path, lines: list[dict[str, Any]], settings: dict[str, Any]
) -> list[float | None]:
    import librosa
    import numpy as np

    sample_rate = int(settings.get("speakerAnalysisSampleRate", 16000))
    hop_length = int(settings.get("speakerPitchHopLength", 320))
    waveform, _ = librosa.load(vocal_path, sr=sample_rate, mono=True)
    f0, voiced, probabilities = librosa.pyin(
        waveform,
        fmin=float(settings.get("speakerPitchMinimumHz", 75.0)),
        fmax=float(settings.get("speakerPitchMaximumHz", 600.0)),
        sr=sample_rate,
        frame_length=int(settings.get("speakerPitchFrameLength", 2048)),
        hop_length=hop_length,
    )
    times = np.arange(len(f0), dtype=np.float64) * hop_length / sample_rate
    minimum_probability = float(settings.get("minimumVoicedProbability", 0.55))
    minimum_frames = int(settings.get("minimumVoicedFrames", 8))
    medians: list[float | None] = []
    for line in lines:
        mask = (
            (times >= float(line["start"]))
            & (times <= float(line["end"]))
            & voiced
            & np.isfinite(f0)
            & np.isfinite(probabilities)
            & (probabilities >= minimum_probability)
        )
        medians.append(
            float(np.median(f0[mask]))
            if int(np.count_nonzero(mask)) >= minimum_frames
            else None
        )
    return medians


def _speaker_segment_embeddings(
    root: Path,
    paths: list[Path],
    segments: list[dict[str, Any]],
    settings: dict[str, Any],
    *,
    context_seconds: float = 0.0,
    minimum_segment_seconds: float = 0.25,
) -> list[Any]:
    import librosa
    import numpy as np
    import torch
    from transformers import AutoFeatureExtractor, WavLMForXVector

    sample_rate = int(settings.get("speakerAnalysisSampleRate", 16000))
    clips: list[Any] = []
    path_counts: list[int] = []
    for path in paths:
        waveform, _ = librosa.load(path, sr=sample_rate, mono=True)
        current: list[Any] = []
        for segment in segments:
            raw_start = float(segment["start"])
            raw_end = float(segment["end"])
            center = (raw_start + raw_end) / 2
            half_duration = max(
                (raw_end - raw_start) / 2 + context_seconds,
                minimum_segment_seconds / 2,
            )
            start = max(0, round((center - half_duration) * sample_rate))
            end = min(len(waveform), round((center + half_duration) * sample_rate))
            clip = waveform[start:end]
            if len(clip) < sample_rate // 4:
                clip = np.pad(clip, (0, sample_rate // 4 - len(clip)))
            current.append(clip)
        clips.extend(current)
        path_counts.append(len(current))

    model_id = str(
        settings.get("speakerEmbeddingModel", "microsoft/wavlm-base-plus-sv")
    )
    model_revision = str(settings.get("speakerEmbeddingModelRevision", "")).strip()
    if not model_revision:
        raise ValueError(
            "speakerEmbeddingModelRevision must pin an exact Hugging Face commit"
        )
    model_cache = root / "models" / "huggingface"
    resolved_model: str | Path = (
        _snapshot_path(model_cache, model_id, model_revision) or model_id
    )
    load_options: dict[str, Any] = {"cache_dir": model_cache}
    if isinstance(resolved_model, Path):
        load_options["local_files_only"] = True
    else:
        load_options["revision"] = model_revision
    extractor = AutoFeatureExtractor.from_pretrained(resolved_model, **load_options)
    device = "cuda" if torch.cuda.is_available() else "cpu"
    model = WavLMForXVector.from_pretrained(resolved_model, **load_options).eval().to(device)
    batch_size = int(settings.get("speakerEmbeddingBatchSize", 8))
    embeddings: list[Any] = []
    for start in range(0, len(clips), batch_size):
        batch = extractor(
            clips[start : start + batch_size],
            sampling_rate=sample_rate,
            padding=True,
            return_tensors="pt",
        )
        values = batch.input_values.to(device)
        attention_mask = (
            batch.attention_mask.to(device)
            if "attention_mask" in batch
            else None
        )
        with torch.inference_mode():
            output = model(
                input_values=values, attention_mask=attention_mask
            ).embeddings
            output = torch.nn.functional.normalize(output, dim=-1)
        embeddings.extend(output.cpu().numpy())
    del model, extractor
    if device == "cuda":
        torch.cuda.empty_cache()
    result: list[Any] = []
    cursor = 0
    for count in path_counts:
        result.append(np.stack(embeddings[cursor : cursor + count]))
        cursor += count
    return result


def _speaker_line_embeddings(
    root: Path,
    paths: list[Path],
    lines: list[dict[str, Any]],
    settings: dict[str, Any],
) -> list[Any]:
    return _speaker_segment_embeddings(root, paths, lines, settings)


def _colead_lexical_evidence(
    root: Path,
    lead_path: Path,
    backing_path: Path,
    lines: list[dict[str, Any]],
    settings: dict[str, Any],
) -> list[dict[str, Any]]:
    aligner = get_vietnamese_song_aligner(root, settings)
    lead_waveform = aligner.load_audio(lead_path)
    backing_waveform = aligner.load_audio(backing_path)
    duration = lead_waveform.shape[1] / 16000
    window_padding = float(settings.get("coLeadAlignmentWindowPaddingSeconds", 0.65))
    word_confidence = float(settings.get("minimumCoLeadWordConfidence", 0.35))
    word_ratio = float(settings.get("minimumCoLeadWordConfidenceRatio", 0.35))
    word_onset_delta = float(settings.get("maximumCoLeadWordOnsetDeltaSeconds", 0.65))
    consonant_confidence = float(
        settings.get("minimumCoLeadConsonantConfidence", 0.2)
    )
    consonant_ratio = float(
        settings.get("minimumCoLeadConsonantConfidenceRatio", 0.35)
    )
    foreground_window_fraction = float(
        settings.get("coLeadForegroundWindowFraction", 0.55)
    )
    minimum_foreground_window = float(
        settings.get("minimumCoLeadForegroundWindowSeconds", 0.2)
    )
    maximum_foreground_window = float(
        settings.get("maximumCoLeadForegroundWindowSeconds", 0.55)
    )

    def foreground_rms_dbfs(
        waveform: Any, start_seconds: float, end_seconds: float
    ) -> float:
        start_sample = max(0, round(start_seconds * 16000))
        end_sample = min(waveform.shape[1], round(end_seconds * 16000))
        if end_sample <= start_sample:
            return -180.0
        clip = waveform[0, start_sample:end_sample]
        rms = float(clip.square().mean().sqrt().item())
        return 20.0 * math.log10(max(rms, 1e-9))

    line_words: list[list[str]] = []
    for index, line in enumerate(lines, start=1):
        words = [str(item["text"]) for item in line.get("syllables", [])]
        if not words:
            words = str(line.get("text", "")).split()
        if not words:
            raise ValueError(f"Lyric line {index} has no words for co-lead analysis")
        line_words.append(words)

    line_alignments: list[tuple[list[dict[str, Any]], list[dict[str, Any]]]] = [
        ([], []) for _ in lines
    ]
    if bool(settings.get("coLeadSemanticGroupAlignment", True)):
        grouped_indexes: dict[int, list[int]] = {}
        for index, line in enumerate(lines):
            group = int(line.get("referenceGroup", index + 1))
            grouped_indexes.setdefault(group, []).append(index)
        for indexes in grouped_indexes.values():
            grouped_words = [word for index in indexes for word in line_words[index]]
            window_start = max(
                0.0, float(lines[indexes[0]]["start"]) - window_padding
            )
            window_end = min(
                duration, float(lines[indexes[-1]]["end"]) + window_padding
            )
            lead_group = aligner.align_window(
                lead_waveform,
                window_start=window_start,
                window_end=window_end,
                raw_words=grouped_words,
            )
            backing_group = aligner.align_window(
                backing_waveform,
                window_start=window_start,
                window_end=window_end,
                raw_words=grouped_words,
            )
            cursor = 0
            for index in indexes:
                count = len(line_words[index])
                line_alignments[index] = (
                    lead_group[cursor : cursor + count],
                    backing_group[cursor : cursor + count],
                )
                cursor += count
    else:
        for index, (line, words) in enumerate(zip(lines, line_words)):
            window_start = max(0.0, float(line["start"]) - window_padding)
            window_end = min(duration, float(line["end"]) + window_padding)
            line_alignments[index] = (
                aligner.align_window(
                    lead_waveform,
                    window_start=window_start,
                    window_end=window_end,
                    raw_words=words,
                ),
                aligner.align_window(
                    backing_waveform,
                    window_start=window_start,
                    window_end=window_end,
                    raw_words=words,
                ),
            )

    evidence: list[dict[str, Any]] = []
    for index, (line, words, alignment) in enumerate(
        zip(lines, line_words, line_alignments), start=1
    ):
        lead, backing = alignment
        lead_mean = sum(float(item["confidence"]) for item in lead) / len(lead)
        backing_mean = sum(float(item["confidence"]) for item in backing) / len(backing)
        onset_deltas = [
            abs(float(left["start"]) - float(right["start"]))
            for left, right in zip(lead, backing)
        ]
        matched = [
            delta <= word_onset_delta
            and float(right["confidence"]) >= word_confidence
            and float(right["confidence"]) >= word_ratio * float(left["confidence"])
            for left, right, delta in zip(lead, backing, onset_deltas)
        ]
        lead_consonants = [
            float(score)
            for item in lead
            for score in item.get("consonantConfidences", [])
        ]
        backing_consonants = [
            float(score)
            for item in backing
            for score in item.get("consonantConfidences", [])
        ]
        if len(lead_consonants) != len(backing_consonants):
            raise RuntimeError(
                f"Co-lead consonant evidence is inconsistent on lyric line {index}"
            )
        supported_consonants = [
            right >= consonant_confidence and right >= consonant_ratio * left
            for left, right in zip(lead_consonants, backing_consonants)
        ]
        word_evidence: list[dict[str, Any]] = []
        timed_words = list(line.get("syllables", []))
        if timed_words and len(timed_words) != len(words):
            raise RuntimeError(
                f"Co-lead prominence evidence is inconsistent on lyric line {index}"
            )
        for word_index, (word, left, right, delta, is_matched) in enumerate(zip(
            words, lead, backing, onset_deltas, matched
        )):
            timed_word = timed_words[word_index] if timed_words else {}
            prominence_start = float(
                timed_word.get(
                    "start", min(float(left["start"]), float(right["start"]))
                )
            )
            fallback_end = max(
                float(left.get("acousticEnd", left.get("end", prominence_start))),
                float(right.get("acousticEnd", right.get("end", prominence_start))),
                prominence_start + minimum_foreground_window,
            )
            word_end = max(
                prominence_start,
                float(timed_word.get("end", fallback_end)),
            )
            word_duration = max(0.0, word_end - prominence_start)
            prominence_duration = min(
                word_duration,
                maximum_foreground_window,
                max(
                    minimum_foreground_window,
                    word_duration * foreground_window_fraction,
                ),
            )
            prominence_end = prominence_start + prominence_duration
            lead_rms_dbfs = foreground_rms_dbfs(
                lead_waveform, prominence_start, prominence_end
            )
            backing_rms_dbfs = foreground_rms_dbfs(
                backing_waveform, prominence_start, prominence_end
            )
            backing_to_lead_rms_db = backing_rms_dbfs - lead_rms_dbfs
            left_consonants = [
                float(score) for score in left.get("consonantConfidences", [])
            ]
            right_consonants = [
                float(score) for score in right.get("consonantConfidences", [])
            ]
            supported = [
                backing_score >= consonant_confidence
                and backing_score >= consonant_ratio * lead_score
                for lead_score, backing_score in zip(
                    left_consonants, right_consonants
                )
            ]
            mean_left = (
                sum(left_consonants) / len(left_consonants)
                if left_consonants
                else 0.0
            )
            mean_right = (
                sum(right_consonants) / len(right_consonants)
                if right_consonants
                else 0.0
            )
            word_evidence.append(
                {
                    "text": word,
                    "matched": bool(is_matched),
                    "leadStart": round(float(left["start"]), 4),
                    "backingStart": round(float(right["start"]), 4),
                    "onsetDeltaSeconds": round(delta, 4),
                    "leadConfidence": round(float(left["confidence"]), 4),
                    "backingConfidence": round(float(right["confidence"]), 4),
                    "backingToLeadConfidenceRatio": round(
                        float(right["confidence"])
                        / max(float(left["confidence"]), 1e-9),
                        4,
                    ),
                    "consonantCount": len(right_consonants),
                    "supportedConsonantCount": sum(supported),
                    "consonantCoverage": round(
                        sum(supported) / max(1, len(right_consonants)), 4
                    ),
                    "meanLeadConsonantConfidence": round(mean_left, 4),
                    "meanBackingConsonantConfidence": round(mean_right, 4),
                    "backingToLeadConsonantConfidenceRatio": round(
                        mean_right / max(mean_left, 1e-9), 4
                    ),
                    "foregroundWindowStart": round(prominence_start, 4),
                    "foregroundWindowEnd": round(prominence_end, 4),
                    "leadForegroundRmsDbfs": round(lead_rms_dbfs, 4),
                    "backingForegroundRmsDbfs": round(backing_rms_dbfs, 4),
                    "backingToLeadForegroundRmsDb": round(
                        backing_to_lead_rms_db, 4
                    ),
                }
            )
        mean_lead_consonant = (
            sum(lead_consonants) / len(lead_consonants)
            if lead_consonants
            else 0.0
        )
        mean_backing_consonant = (
            sum(backing_consonants) / len(backing_consonants)
            if backing_consonants
            else 0.0
        )
        evidence.append(
            {
                "line": index,
                "wordCount": len(words),
                "matchedWordCount": sum(matched),
                "lexicalCoverage": round(sum(matched) / len(words), 4),
                "meanLeadConfidence": round(lead_mean, 4),
                "meanBackingConfidence": round(backing_mean, 4),
                "backingToLeadConfidenceRatio": round(
                    backing_mean / max(lead_mean, 1e-9), 4
                ),
                "meanOnsetDeltaSeconds": round(
                    sum(onset_deltas) / len(onset_deltas), 4
                ),
                "consonantCount": len(backing_consonants),
                "supportedConsonantCount": sum(supported_consonants),
                "consonantCoverage": round(
                    sum(supported_consonants) / max(1, len(backing_consonants)), 4
                ),
                "meanLeadConsonantConfidence": round(mean_lead_consonant, 4),
                "meanBackingConsonantConfidence": round(mean_backing_consonant, 4),
                "backingToLeadConsonantConfidenceRatio": round(
                    mean_backing_consonant / max(mean_lead_consonant, 1e-9), 4
                ),
                "wordEvidence": word_evidence,
            }
        )
    return evidence


def decide_colead_word_roles(
    lexical_evidence: list[dict[str, Any]],
    settings: dict[str, Any],
    *,
    seed_lines: list[bool] | None = None,
    reference_groups: list[int] | None = None,
) -> tuple[list[list[bool]], dict[str, Any]]:
    """Decode co-lead intervals from word anchors instead of whole lines.

    Strong same-word/opposite-speaker anchors establish the start and end of a
    duet run. Weak words between nearby anchors inherit the state, which keeps
    ordinary CTC misses from chopping one continuous phrase into conflicting
    line colors. A lone anchor never creates a duet interval.
    """
    flattened: list[dict[str, Any]] = []
    nested = [
        [False] * len(line.get("wordEvidence", []))
        for line in lexical_evidence
    ]
    if seed_lines is not None and len(seed_lines) != len(lexical_evidence):
        raise ValueError("Word co-lead seed lines must match the lyric line count")
    semantic_groups_provided = reference_groups is not None
    if reference_groups is None:
        reference_groups = list(range(1, len(lexical_evidence) + 1))
    if len(reference_groups) != len(lexical_evidence):
        raise ValueError("Word co-lead phrase groups must match the lyric line count")
    minimum_consonant_coverage = float(
        settings.get("minimumCoLeadWordConsonantCoverage", 0.6)
    )
    minimum_consonant_confidence = float(
        settings.get("minimumCoLeadMeanConsonantConfidence", 0.2)
    )
    minimum_consonant_ratio = float(
        settings.get("minimumCoLeadConsonantConfidenceRatio", 0.35)
    )
    for line_index, line in enumerate(lexical_evidence):
        for word_index, word in enumerate(line.get("wordEvidence", [])):
            lead_role = word.get("leadWordRole")
            backing_role = word.get("backingWordRole")
            lead_cluster = word.get("leadWordCluster")
            backing_cluster = word.get("backingWordCluster")
            distinct_speakers = bool(
                lead_role in {"male", "female"}
                and backing_role in {"male", "female"}
                and (
                    lead_cluster != backing_cluster
                    if isinstance(lead_cluster, int)
                    and isinstance(backing_cluster, int)
                    else lead_role != backing_role
                )
            )
            anchor = (
                bool(word.get("matched", False))
                and distinct_speakers
                and int(word.get("consonantCount", 0)) >= 1
                and float(word.get("consonantCoverage", 0.0))
                >= minimum_consonant_coverage
                and float(word.get("meanBackingConsonantConfidence", 0.0))
                >= minimum_consonant_confidence
                and float(
                    word.get("backingToLeadConsonantConfidenceRatio", 0.0)
                )
                >= minimum_consonant_ratio
            )
            flattened.append(
                {
                    "lineIndex": line_index,
                    "wordIndex": word_index,
                    "referenceGroup": int(reference_groups[line_index]),
                    "text": str(word.get("text", "")),
                    "start": float(word.get("leadStart", 0.0)),
                    "oppositeSpeakers": (
                        distinct_speakers
                    ),
                    "anchor": anchor,
                }
            )

    maximum_gap_words = int(settings.get("maximumCoLeadAnchorGapWords", 6))
    maximum_gap_seconds = float(
        settings.get("maximumCoLeadAnchorGapSeconds", 4.5)
    )
    minimum_opposite_ratio = float(
        settings.get("minimumCoLeadRunOppositeSpeakerRatio", 0.5)
    )
    anchor_indexes = [
        index for index, item in enumerate(flattened) if bool(item["anchor"])
    ]
    runs: list[list[int]] = []
    current: list[int] = []
    for anchor_index in anchor_indexes:
        if not current:
            current = [anchor_index]
            continue
        previous = current[-1]
        interval = flattened[previous : anchor_index + 1]
        word_gap = anchor_index - previous - 1
        time_gap = float(flattened[anchor_index]["start"]) - float(
            flattened[previous]["start"]
        )
        opposite_ratio = sum(
            bool(item["oppositeSpeakers"]) for item in interval
        ) / len(interval)
        if (
            word_gap <= maximum_gap_words
            and time_gap <= maximum_gap_seconds
            and opposite_ratio >= minimum_opposite_ratio
        ):
            current.append(anchor_index)
        else:
            if len(current) >= 2:
                runs.append(current)
            current = [anchor_index]
    if len(current) >= 2:
        runs.append(current)

    decoded_ranges: list[dict[str, Any]] = []
    semantic_tail_extensions: list[dict[str, Any]] = []
    ambiguous_semantic_tails: list[dict[str, Any]] = []
    rejected_unseeded_ranges: list[dict[str, Any]] = []
    foreground_verifiable_unseeded_ranges: list[dict[str, Any]] = []
    rejected_partial_phrase_ranges: list[dict[str, Any]] = []
    minimum_tail_anchors = int(
        settings.get("minimumCoLeadSemanticTailAnchorCount", 3)
    )
    minimum_tail_opposite_ratio = float(
        settings.get("minimumCoLeadSemanticTailOppositeSpeakerRatio", 0.66)
    )
    maximum_solo_tail_ratio = float(
        settings.get("maximumCoLeadSemanticTailSoloSpeakerRatio", 0.33)
    )
    maximum_tail_word_gap = float(
        settings.get("maximumCoLeadSemanticTailWordGapSeconds", 1.5)
    )
    allow_foreground_verified_unseeded_ranges = bool(
        settings.get("allowForegroundVerifiedUnseededCoLeadRanges", False)
    )
    for run in runs:
        left, right = run[0], run[-1]
        group = int(flattened[right]["referenceGroup"])
        seed_line_indexes = (
            []
            if seed_lines is None
            else [
                line_index
                for line_index, is_seed in enumerate(seed_lines)
                if is_seed and int(reference_groups[line_index]) == group
            ]
        )
        overlaps_seed = seed_lines is None or bool(seed_line_indexes)
        if not overlaps_seed:
            unseeded_range = {
                "referenceGroup": group,
                "startLine": int(flattened[left]["lineIndex"]) + 1,
                "startWord": int(flattened[left]["wordIndex"]) + 1,
                "startText": flattened[left]["text"],
                "endLine": int(flattened[right]["lineIndex"]) + 1,
                "endWord": int(flattened[right]["wordIndex"]) + 1,
                "endText": flattened[right]["text"],
                "anchorCount": len(run),
                "wordCount": right - left + 1,
            }
            if allow_foreground_verified_unseeded_ranges:
                foreground_verifiable_unseeded_ranges.append(
                    {
                        **unseeded_range,
                        "status": "candidate-requires-independent-foreground-gate",
                    }
                )
            else:
                rejected_unseeded_ranges.append(
                    {
                        **unseeded_range,
                        "reason": "no-independent-line-level-colead-seed",
                    }
                )
                continue
        original_right = right
        semantic_seed_extended = False
        later_seed_lines = [
            line_index
            for line_index in seed_line_indexes
            if line_index > int(flattened[right]["lineIndex"])
        ]
        if later_seed_lines:
            target_seed_line = min(later_seed_lines)
            candidates = [
                index
                for index, item in enumerate(flattened)
                if int(item["referenceGroup"]) == group
                and int(item["lineIndex"]) <= target_seed_line
            ]
            if candidates:
                right = max(right, candidates[-1])
                semantic_seed_extended = right != original_right
                maximum_head_words = int(
                    settings.get("maximumCoLeadSemanticHeadExtensionWords", 1)
                )
                group_indexes = [
                    index
                    for index, item in enumerate(flattened)
                    if int(item["referenceGroup"]) == group
                ]
                if group_indexes and left - group_indexes[0] <= maximum_head_words:
                    candidate_left = group_indexes[0]
                    opposite_ratio = sum(
                        bool(item["oppositeSpeakers"])
                        for item in flattened[candidate_left : right + 1]
                    ) / max(1, right - candidate_left + 1)
                    if opposite_ratio >= minimum_opposite_ratio:
                        left = candidate_left
        tail: list[int] = []
        cursor = right + 1
        previous_start = float(flattened[right]["start"])
        while cursor < len(flattened):
            item = flattened[cursor]
            if int(item["referenceGroup"]) != group:
                break
            if float(item["start"]) - previous_start > maximum_tail_word_gap:
                break
            tail.append(cursor)
            previous_start = float(item["start"])
            cursor += 1
        if len(run) >= minimum_tail_anchors and tail:
            opposite_ratio = sum(
                bool(flattened[index]["oppositeSpeakers"]) for index in tail
            ) / len(tail)
            tail_report = {
                "referenceGroup": group,
                "afterLine": int(flattened[original_right]["lineIndex"]) + 1,
                "fromText": flattened[tail[0]]["text"],
                "toText": flattened[tail[-1]]["text"],
                "wordCount": len(tail),
                "oppositeSpeakerRatio": round(opposite_ratio, 4),
            }
            if opposite_ratio >= minimum_tail_opposite_ratio:
                right = tail[-1]
                semantic_tail_extensions.append(tail_report)
            elif opposite_ratio > maximum_solo_tail_ratio:
                ambiguous_semantic_tails.append(tail_report)
        phrase_coverages: dict[int, float] = {}
        if semantic_groups_provided:
            touched_groups = {
                int(item["referenceGroup"])
                for item in flattened[left : right + 1]
            }
            for touched_group in touched_groups:
                phrase_word_count = sum(
                    int(item["referenceGroup"]) == touched_group
                    for item in flattened
                )
                duet_word_count = sum(
                    int(item["referenceGroup"]) == touched_group
                    for item in flattened[left : right + 1]
                )
                phrase_coverages[touched_group] = (
                    duet_word_count / max(1, phrase_word_count)
                )
            minimum_phrase_coverage = float(
                settings.get("minimumCoLeadSemanticPhraseCoverage", 0.66)
            )
            if (
                overlaps_seed
                and min(phrase_coverages.values(), default=1.0)
                < minimum_phrase_coverage
            ):
                rejected_partial_phrase_ranges.append(
                    {
                        "startText": flattened[left]["text"],
                        "endText": flattened[right]["text"],
                        "coverageByReferenceGroup": {
                            str(key): round(value, 4)
                            for key, value in phrase_coverages.items()
                        },
                        "minimumCoverage": minimum_phrase_coverage,
                    }
                )
                continue
        for item in flattened[left : right + 1]:
            nested[int(item["lineIndex"])][int(item["wordIndex"])] = True
        decoded_ranges.append(
            {
                "startLine": int(flattened[left]["lineIndex"]) + 1,
                "startWord": int(flattened[left]["wordIndex"]) + 1,
                "startText": flattened[left]["text"],
                "endLine": int(flattened[right]["lineIndex"]) + 1,
                "endWord": int(flattened[right]["wordIndex"]) + 1,
                "endText": flattened[right]["text"],
                "anchorCount": len(run),
                "wordCount": right - left + 1,
                "semanticTailExtended": right != original_right,
                "semanticSeedExtended": semantic_seed_extended,
                "lineSeeded": overlaps_seed,
                "coverageByReferenceGroup": {
                    str(key): round(value, 4)
                    for key, value in phrase_coverages.items()
                },
            }
        )
    return nested, {
        "policy": "line-seed-plus-word-anchor-and-semantic-tail-continuity",
        "anchorCount": len(anchor_indexes),
        "decodedRanges": decoded_ranges,
        "seedLineCount": sum(seed_lines) if seed_lines is not None else None,
        "rejectedUnseededRangeCount": len(rejected_unseeded_ranges),
        "rejectedUnseededRanges": rejected_unseeded_ranges,
        "foregroundVerifiableUnseededRangeCount": len(
            foreground_verifiable_unseeded_ranges
        ),
        "foregroundVerifiableUnseededRanges": (
            foreground_verifiable_unseeded_ranges
        ),
        "rejectedPartialPhraseRanges": rejected_partial_phrase_ranges,
        "semanticTailExtensions": semantic_tail_extensions,
        "ambiguousSemanticTails": ambiguous_semantic_tails,
        "duetWordCount": sum(sum(line) for line in nested),
    }


def gate_colead_groups_by_foreground_prominence(
    decoded: list[list[bool]],
    lexical_evidence: list[dict[str, Any]],
    reference_groups: list[int],
    settings: dict[str, Any],
    *,
    foreground_verifiable_unseeded_ranges: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Reject backing harmony that merely articulates the lead singer's words.

    Lexical alignment proves that a second singer is present, but a backing
    singer can pronounce every consonant and still not own the karaoke line.
    A duet therefore also requires foreground parity.  Phrase-wide parity is
    preferred, while a sustained multi-line foreground run may retain only its
    locally supported co-lead span.  Measuring the opening portion of every
    syllable avoids treating a long final hold as solo merely because one of
    two co-leads releases the note first.
    """
    if not (
        len(decoded) == len(lexical_evidence) == len(reference_groups)
    ):
        raise ValueError("Co-lead foreground evidence must have equal lengths")
    for line, evidence in zip(decoded, lexical_evidence):
        if len(line) != len(evidence.get("wordEvidence", [])):
            raise ValueError(
                "Co-lead foreground evidence must match every lyric syllable"
            )

    minimum_backing_dbfs = float(
        settings.get("minimumCoLeadBackingForegroundRmsDbfs", -42.0)
    )
    minimum_word_ratio_db = float(
        settings.get("minimumCoLeadBackingToLeadForegroundDb", -9.0)
    )
    minimum_group_coverage = float(
        settings.get("minimumCoLeadGroupForegroundCoverage", 0.75)
    )
    minimum_group_median_ratio_db = float(
        settings.get("minimumCoLeadGroupMedianBackingToLeadDb", -9.0)
    )
    minimum_group_median_backing_dbfs = float(
        settings.get("minimumCoLeadGroupMedianBackingRmsDbfs", -42.0)
    )
    minimum_localized_word_count = int(
        settings.get("minimumCoLeadLocalizedForegroundWordCount", 5)
    )
    minimum_localized_line_count = int(
        settings.get("minimumCoLeadLocalizedForegroundLineCount", 2)
    )
    minimum_localized_coverage = float(
        settings.get("minimumCoLeadLocalizedForegroundCoverage", 0.6)
    )
    maximum_localized_gap_words = int(
        settings.get("maximumCoLeadLocalizedForegroundGapWords", 2)
    )
    minimum_unseeded_word_count = int(
        settings.get("minimumCoLeadUnseededForegroundWordCount", 2)
    )
    minimum_unseeded_coverage = float(
        settings.get("minimumCoLeadUnseededForegroundCoverage", 1.0)
    )

    def median(values: list[float]) -> float:
        ordered = sorted(values)
        middle = len(ordered) // 2
        if len(ordered) % 2:
            return ordered[middle]
        return (ordered[middle - 1] + ordered[middle]) / 2.0

    by_group: dict[int, list[int]] = {}
    for index, group in enumerate(reference_groups):
        by_group.setdefault(int(group), []).append(index)
    accepted: list[dict[str, Any]] = []
    localized: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    unseeded_by_group: dict[int, list[dict[str, Any]]] = {}
    for item in foreground_verifiable_unseeded_ranges or []:
        unseeded_by_group.setdefault(int(item["referenceGroup"]), []).append(item)
    for group, indexes in by_group.items():
        candidate_count = sum(sum(decoded[index]) for index in indexes)
        if not candidate_count:
            continue
        indexed_words = [
            {
                "lineIndex": index,
                "wordIndex": word_index,
                "word": word,
                "candidate": bool(decoded[index][word_index]),
            }
            for index in indexes
            for word_index, word in enumerate(
                lexical_evidence[index].get("wordEvidence", [])
            )
        ]
        words = [item["word"] for item in indexed_words]
        available = [
            word
            for word in words
            if isinstance(word.get("backingForegroundRmsDbfs"), (int, float))
            and isinstance(
                word.get("backingToLeadForegroundRmsDb"), (int, float)
            )
        ]
        reasons: list[str] = []
        if len(available) != len(words) or not available:
            reasons.append("missing-foreground-prominence")
            coverage = 0.0
            median_ratio_db = -180.0
            median_backing_dbfs = -180.0
            prominent_count = 0
        else:
            ratios = [
                float(word["backingToLeadForegroundRmsDb"])
                for word in available
            ]
            backing_levels = [
                float(word["backingForegroundRmsDbfs"])
                for word in available
            ]
            prominent_count = sum(
                level >= minimum_backing_dbfs and ratio >= minimum_word_ratio_db
                for level, ratio in zip(backing_levels, ratios)
            )
            coverage = prominent_count / len(available)
            median_ratio_db = median(ratios)
            median_backing_dbfs = median(backing_levels)
            if coverage < minimum_group_coverage:
                reasons.append("insufficient-phrase-wide-foreground-coverage")
            if median_ratio_db < minimum_group_median_ratio_db:
                reasons.append("secondary-voice-not-foreground-balanced")
            if median_backing_dbfs < minimum_group_median_backing_dbfs:
                reasons.append("secondary-voice-below-foreground-activity")
        diagnostic = {
            "referenceGroup": group,
            "wordCount": len(words),
            "candidateDuetWordCount": candidate_count,
            "foregroundWordCount": prominent_count,
            "foregroundCoverage": round(coverage, 4),
            "medianBackingToLeadForegroundDb": round(median_ratio_db, 4),
            "medianBackingForegroundRmsDbfs": round(median_backing_dbfs, 4),
        }
        if reasons:
            prominent_positions = [
                position
                for position, item in enumerate(indexed_words)
                if item["candidate"]
                and isinstance(
                    item["word"].get("backingForegroundRmsDbfs"),
                    (int, float),
                )
                and isinstance(
                    item["word"].get("backingToLeadForegroundRmsDb"),
                    (int, float),
                )
                and float(item["word"]["backingForegroundRmsDbfs"])
                >= minimum_backing_dbfs
                and float(item["word"]["backingToLeadForegroundRmsDb"])
                >= minimum_word_ratio_db
            ]
            prominent_runs: list[list[int]] = []
            for position in prominent_positions:
                if (
                    not prominent_runs
                    or position - prominent_runs[-1][-1] - 1
                    > maximum_localized_gap_words
                ):
                    prominent_runs.append([position])
                else:
                    prominent_runs[-1].append(position)

            retained_positions: set[int] = set()
            localized_segments: list[dict[str, Any]] = []
            for run in prominent_runs:
                if len(run) < minimum_localized_word_count:
                    continue
                start_position = run[0]
                end_position = run[-1]
                span = indexed_words[start_position : end_position + 1]
                if not span or not all(item["candidate"] for item in span):
                    continue
                line_count = len({int(item["lineIndex"]) for item in span})
                span_coverage = len(run) / len(span)
                span_ratios = [
                    float(item["word"]["backingToLeadForegroundRmsDb"])
                    for item in span
                ]
                span_backing_levels = [
                    float(item["word"]["backingForegroundRmsDbfs"])
                    for item in span
                ]
                span_median_ratio_db = median(span_ratios)
                span_median_backing_dbfs = median(span_backing_levels)
                if (
                    line_count < minimum_localized_line_count
                    or span_coverage < minimum_localized_coverage
                    or span_median_ratio_db < minimum_group_median_ratio_db
                    or span_median_backing_dbfs
                    < minimum_group_median_backing_dbfs
                ):
                    continue
                retained_start_position = start_position
                while (
                    retained_start_position > 0
                    and indexed_words[retained_start_position - 1]["candidate"]
                ):
                    retained_start_position -= 1
                retained_end_position = end_position
                while (
                    retained_end_position + 1 < len(indexed_words)
                    and indexed_words[retained_end_position + 1]["candidate"]
                ):
                    retained_end_position += 1
                retained_span = indexed_words[
                    retained_start_position : retained_end_position + 1
                ]
                retained_positions.update(
                    range(retained_start_position, retained_end_position + 1)
                )
                localized_segments.append(
                    {
                        "startLine": int(retained_span[0]["lineIndex"]) + 1,
                        "startWord": int(retained_span[0]["wordIndex"]) + 1,
                        "startText": str(
                            retained_span[0]["word"].get("text", "")
                        ),
                        "endLine": int(retained_span[-1]["lineIndex"]) + 1,
                        "endWord": int(retained_span[-1]["wordIndex"]) + 1,
                        "endText": str(
                            retained_span[-1]["word"].get("text", "")
                        ),
                        "wordCount": len(retained_span),
                        "foregroundEvidenceStartLine": int(
                            span[0]["lineIndex"]
                        )
                        + 1,
                        "foregroundEvidenceStartWord": int(
                            span[0]["wordIndex"]
                        )
                        + 1,
                        "foregroundEvidenceStartText": str(
                            span[0]["word"].get("text", "")
                        ),
                        "foregroundEvidenceEndLine": int(
                            span[-1]["lineIndex"]
                        )
                        + 1,
                        "foregroundEvidenceEndWord": int(
                            span[-1]["wordIndex"]
                        )
                        + 1,
                        "foregroundEvidenceEndText": str(
                            span[-1]["word"].get("text", "")
                        ),
                        "foregroundEvidenceWordCount": len(span),
                        "foregroundWordCount": len(run),
                        "foregroundCoverage": round(span_coverage, 4),
                        "lineCount": line_count,
                        "medianBackingToLeadForegroundDb": round(
                            span_median_ratio_db, 4
                        ),
                        "medianBackingForegroundRmsDbfs": round(
                            span_median_backing_dbfs, 4
                        ),
                    }
                )

            for unseeded_range in unseeded_by_group.get(group, []):
                start_key = (
                    int(unseeded_range["startLine"]),
                    int(unseeded_range["startWord"]),
                )
                end_key = (
                    int(unseeded_range["endLine"]),
                    int(unseeded_range["endWord"]),
                )
                range_positions = [
                    position
                    for position, item in enumerate(indexed_words)
                    if start_key
                    <= (
                        int(item["lineIndex"]) + 1,
                        int(item["wordIndex"]) + 1,
                    )
                    <= end_key
                ]
                if len(range_positions) < minimum_unseeded_word_count:
                    continue
                range_items = [indexed_words[position] for position in range_positions]
                if not all(item["candidate"] for item in range_items):
                    continue
                range_prominent_positions = [
                    position
                    for position in range_positions
                    if position in prominent_positions
                ]
                range_coverage = len(range_prominent_positions) / len(
                    range_positions
                )
                range_ratios = [
                    float(item["word"]["backingToLeadForegroundRmsDb"])
                    for item in range_items
                ]
                range_backing_levels = [
                    float(item["word"]["backingForegroundRmsDbfs"])
                    for item in range_items
                ]
                range_median_ratio_db = median(range_ratios)
                range_median_backing_dbfs = median(range_backing_levels)
                if (
                    range_coverage < minimum_unseeded_coverage
                    or range_median_ratio_db < minimum_group_median_ratio_db
                    or range_median_backing_dbfs
                    < minimum_group_median_backing_dbfs
                ):
                    continue
                retained_positions.update(range_positions)
                localized_segments.append(
                    {
                        "confirmationPolicy": (
                            "unseeded-word-speaker-plus-full-foreground-consensus"
                        ),
                        "startLine": int(range_items[0]["lineIndex"]) + 1,
                        "startWord": int(range_items[0]["wordIndex"]) + 1,
                        "startText": str(
                            range_items[0]["word"].get("text", "")
                        ),
                        "endLine": int(range_items[-1]["lineIndex"]) + 1,
                        "endWord": int(range_items[-1]["wordIndex"]) + 1,
                        "endText": str(
                            range_items[-1]["word"].get("text", "")
                        ),
                        "wordCount": len(range_items),
                        "foregroundWordCount": len(range_prominent_positions),
                        "foregroundCoverage": round(range_coverage, 4),
                        "lineCount": len(
                            {int(item["lineIndex"]) for item in range_items}
                        ),
                        "medianBackingToLeadForegroundDb": round(
                            range_median_ratio_db, 4
                        ),
                        "medianBackingForegroundRmsDbfs": round(
                            range_median_backing_dbfs, 4
                        ),
                    }
                )

            removed_count = 0
            for index in indexes:
                removed_count += sum(decoded[index])
                decoded[index] = [False] * len(decoded[index])
            for position in retained_positions:
                item = indexed_words[position]
                decoded[int(item["lineIndex"])][int(item["wordIndex"])] = True
            if localized_segments:
                retained_count = len(retained_positions)
                localized.append(
                    {
                        **diagnostic,
                        "retainedDuetWordCount": retained_count,
                        "removedDuetWordCount": removed_count - retained_count,
                        "phraseWideRejectionReasons": reasons,
                        "segments": localized_segments,
                    }
                )
            else:
                rejected.append(
                    {
                        **diagnostic,
                        "removedDuetWordCount": removed_count,
                        "reasons": reasons,
                    }
                )
        else:
            accepted.append(diagnostic)
    return {
        "policy": "same-lyrics-plus-phrase-wide-or-localized-foreground-parity",
        "acceptedGroups": accepted,
        "localizedAcceptedGroups": localized,
        "rejectedBackingHarmonyGroups": rejected,
        "duetWordCountAfterGate": sum(sum(line) for line in decoded),
    }


def regularize_colead_to_sung_clause_boundaries(
    decoded: list[list[bool]],
    lines: list[dict[str, Any]],
    semantic_clause_mapping: list[list[int]],
    settings: dict[str, Any],
) -> list[dict[str, Any]]:
    """Prevent ordinary role changes in the middle of one sung clause.

    Co-lead decoding is intentionally word-sensitive, but a few missed or
    leaked words must not make a karaoke singer change color halfway through a
    continuous lyric clause.  Punctuation-delimited clauses are decided as a
    unit.  A role boundary inside a clause is retained only when the alignment
    contains an independently measurable vocal pause at every transition.

    This operates after lexical, speaker-identity, and foreground-prominence
    gates.  It therefore regularizes already verified evidence; it never turns
    a merely lexical backing harmony into duet evidence.
    """
    if not (
        len(decoded) == len(lines) == len(semantic_clause_mapping)
    ):
        raise ValueError("Sung-clause role evidence must have equal line counts")
    clause_words: dict[int, list[tuple[int, int]]] = {}
    for line_index, (states, line, mapping) in enumerate(
        zip(decoded, lines, semantic_clause_mapping)
    ):
        syllables = line.get("syllables", [])
        if not (len(states) == len(syllables) == len(mapping)):
            raise ValueError(
                "Sung-clause role evidence must match every lyric syllable"
            )
        for word_index, clause_index in enumerate(mapping):
            clause_words.setdefault(int(clause_index), []).append(
                (line_index, word_index)
            )

    minimum_coverage = float(
        settings.get("minimumCoLeadSungClauseCoverage", 0.75)
    )
    minimum_word_count = int(
        settings.get("minimumCoLeadSungClauseWordCount", 3)
    )
    minimum_pause = float(
        settings.get("minimumRoleBoundaryAcousticPauseSeconds", 0.18)
    )
    report: list[dict[str, Any]] = []
    for clause_index, references in clause_words.items():
        states = [decoded[line][word] for line, word in references]
        if not any(states) or all(states):
            continue
        transitions = [
            index
            for index in range(1, len(states))
            if states[index] != states[index - 1]
        ]
        transition_pauses: list[float] = []
        for position in transitions:
            previous_line, previous_word = references[position - 1]
            current_line, current_word = references[position]
            previous = lines[previous_line]["syllables"][previous_word]
            current = lines[current_line]["syllables"][current_word]
            previous_end = float(previous.get("acousticEnd", previous["end"]))
            transition_pauses.append(
                max(0.0, float(current["start"]) - previous_end)
            )
        if transition_pauses and all(
            pause >= minimum_pause for pause in transition_pauses
        ):
            report.append(
                {
                    "clause": clause_index + 1,
                    "status": "preserved-acoustic-boundary",
                    "transitionPauseSeconds": [
                        round(value, 4) for value in transition_pauses
                    ],
                }
            )
            continue

        duet_word_count = sum(states)
        coverage = duet_word_count / len(states)
        resolved = bool(
            duet_word_count >= minimum_word_count
            and coverage >= minimum_coverage
        )
        changed_word_count = 0
        for line_index, word_index in references:
            if decoded[line_index][word_index] == resolved:
                continue
            decoded[line_index][word_index] = resolved
            changed_word_count += 1
        report.append(
            {
                "clause": clause_index + 1,
                "status": "promoted-complete-clause" if resolved else "demoted-transient-overlap",
                "wordCount": len(states),
                "verifiedDuetWordCount": duet_word_count,
                "verifiedCoverage": round(coverage, 4),
                "changedWordCount": changed_word_count,
                "transitionPauseSeconds": [
                    round(value, 4) for value in transition_pauses
                ],
            }
        )
    return report


def promote_opposite_gender_colead_clauses(
    decoded: list[list[bool]],
    gender_lexical_evidence: list[dict[str, Any]],
    semantic_clause_mapping: list[list[int]],
    settings: dict[str, Any],
) -> list[dict[str, Any]]:
    """Confirm opposite-gender duet clauses with an independent separator.

    A lead/backing separator can place two equally prominent singers in one
    lead stem.  In that case the ordinary backing-vocal proof disappears even
    though both singers articulate the complete lyric.  A dedicated
    male/female separator supplies an independent consensus path.  Its stem
    labels are never used to assign a solo role; it may only promote a complete
    sung clause to duet when both stems are active, lexically aligned, and
    foreground-balanced across the clause.
    """
    if not (
        len(decoded)
        == len(gender_lexical_evidence)
        == len(semantic_clause_mapping)
    ):
        raise ValueError(
            "Opposite-gender co-lead evidence must have equal line counts"
        )
    clause_words: dict[int, list[tuple[int, int, dict[str, Any]]]] = {}
    for line_index, (states, evidence, mapping) in enumerate(
        zip(decoded, gender_lexical_evidence, semantic_clause_mapping)
    ):
        words = evidence.get("wordEvidence", [])
        if not (len(states) == len(words) == len(mapping)):
            raise ValueError(
                "Opposite-gender co-lead evidence must match every lyric syllable"
            )
        for word_index, clause_index in enumerate(mapping):
            clause_words.setdefault(int(clause_index), []).append(
                (line_index, word_index, words[word_index])
            )

    minimum_words = int(
        settings.get("minimumOppositeGenderCoLeadClauseWords", 3)
    )
    minimum_lexical_coverage = float(
        settings.get("minimumOppositeGenderCoLeadLexicalCoverage", 0.75)
    )
    minimum_consonant_coverage = float(
        settings.get("minimumOppositeGenderCoLeadConsonantCoverage", 0.6)
    )
    minimum_balanced_coverage = float(
        settings.get("minimumOppositeGenderCoLeadBalancedWordCoverage", 0.75)
    )
    maximum_word_imbalance_db = float(
        settings.get("maximumOppositeGenderCoLeadWordImbalanceDb", 5.5)
    )
    maximum_median_imbalance_db = float(
        settings.get("maximumOppositeGenderCoLeadMedianImbalanceDb", 3.0)
    )
    minimum_foreground_dbfs = float(
        settings.get("minimumOppositeGenderCoLeadForegroundRmsDbfs", -42.0)
    )

    def median(values: list[float]) -> float:
        ordered = sorted(values)
        middle = len(ordered) // 2
        if len(ordered) % 2:
            return ordered[middle]
        return (ordered[middle - 1] + ordered[middle]) / 2.0

    accepted: list[dict[str, Any]] = []
    for clause_index, references in clause_words.items():
        words = [item[2] for item in references]
        word_count = len(words)
        if word_count < minimum_words:
            continue
        matched_word_count = sum(bool(word.get("matched", False)) for word in words)
        lexical_coverage = matched_word_count / word_count
        consonant_count = sum(int(word.get("consonantCount", 0)) for word in words)
        supported_consonant_count = sum(
            int(word.get("supportedConsonantCount", 0)) for word in words
        )
        consonant_coverage = supported_consonant_count / max(1, consonant_count)
        usable = [
            word
            for word in words
            if all(
                isinstance(word.get(field), (int, float))
                for field in (
                    "leadForegroundRmsDbfs",
                    "backingForegroundRmsDbfs",
                    "backingToLeadForegroundRmsDb",
                )
            )
        ]
        if len(usable) != word_count:
            continue
        ratios = [
            float(word["backingToLeadForegroundRmsDb"]) for word in usable
        ]
        balanced_word_count = sum(
            abs(float(word["backingToLeadForegroundRmsDb"]))
            <= maximum_word_imbalance_db
            and float(word["leadForegroundRmsDbfs"])
            >= minimum_foreground_dbfs
            and float(word["backingForegroundRmsDbfs"])
            >= minimum_foreground_dbfs
            for word in usable
        )
        balanced_coverage = balanced_word_count / word_count
        median_imbalance_db = median(ratios)
        if (
            lexical_coverage < minimum_lexical_coverage
            or consonant_coverage < minimum_consonant_coverage
            or balanced_coverage < minimum_balanced_coverage
            or abs(median_imbalance_db) > maximum_median_imbalance_db
        ):
            continue
        changed_word_count = 0
        for line_index, word_index, _ in references:
            if decoded[line_index][word_index]:
                continue
            decoded[line_index][word_index] = True
            changed_word_count += 1
        accepted.append(
            {
                "clause": clause_index + 1,
                "wordCount": word_count,
                "matchedWordCount": matched_word_count,
                "lexicalCoverage": round(lexical_coverage, 4),
                "consonantCoverage": round(consonant_coverage, 4),
                "balancedWordCoverage": round(balanced_coverage, 4),
                "medianFemaleToMaleForegroundDb": round(
                    median_imbalance_db, 4
                ),
                "changedWordCount": changed_word_count,
                "policy": "independent-male-female-full-clause-consensus",
            }
        )
    return accepted


def _extend_seeded_colead_semantic_groups(
    decoded: list[list[bool]],
    phrase_lexical_evidence: list[dict[str, Any]],
    lead_roles: list[str],
    backing_roles: list[str | None],
    seed_lines: list[bool],
    reference_groups: list[int],
    settings: dict[str, Any],
) -> list[dict[str, Any]]:
    """Fill a same-speaker semantic phrase when aggregate lexical proof is strong."""
    if not (
        len(decoded)
        == len(phrase_lexical_evidence)
        == len(lead_roles)
        == len(backing_roles)
        == len(seed_lines)
        == len(reference_groups)
    ):
        raise ValueError("Seeded semantic-group co-lead evidence must have equal lengths")
    minimum_lexical_coverage = float(
        settings.get("minimumCoLeadSemanticPhraseCoverage", 0.66)
    )
    minimum_consonant_coverage = float(
        settings.get("minimumCoLeadSemanticGroupConsonantCoverage", 0.6)
    )
    by_group: dict[int, list[int]] = {}
    for index, group in enumerate(reference_groups):
        by_group.setdefault(int(group), []).append(index)
    extensions: list[dict[str, Any]] = []
    for group, indexes in by_group.items():
        if not any(seed_lines[index] for index in indexes):
            continue
        distinct_indexes = []
        for index in indexes:
            evidence = phrase_lexical_evidence[index]
            lead_cluster = evidence.get("leadSpeakerCluster")
            backing_cluster = evidence.get("backingSpeakerCluster")
            distinct_indexes.append(
                lead_roles[index] in {"male", "female"}
                and backing_roles[index] in {"male", "female"}
                and (
                    lead_cluster != backing_cluster
                    if isinstance(lead_cluster, int)
                    and isinstance(backing_cluster, int)
                    else lead_roles[index] != backing_roles[index]
                )
            )
        if not all(distinct_indexes):
            continue
        word_count = sum(
            int(phrase_lexical_evidence[index].get("wordCount", 0))
            for index in indexes
        )
        matched_word_count = sum(
            int(phrase_lexical_evidence[index].get("matchedWordCount", 0))
            for index in indexes
        )
        consonant_count = sum(
            int(phrase_lexical_evidence[index].get("consonantCount", 0))
            for index in indexes
        )
        supported_consonant_count = sum(
            int(phrase_lexical_evidence[index].get("supportedConsonantCount", 0))
            for index in indexes
        )
        lexical_coverage = matched_word_count / max(1, word_count)
        consonant_coverage = supported_consonant_count / max(1, consonant_count)
        if (
            lexical_coverage < minimum_lexical_coverage
            or consonant_coverage < minimum_consonant_coverage
        ):
            continue
        changed = sum(not value for index in indexes for value in decoded[index])
        if not changed:
            continue
        for index in indexes:
            decoded[index] = [True] * len(decoded[index])
        extensions.append(
            {
                "referenceGroup": group,
                "startLine": indexes[0] + 1,
                "endLine": indexes[-1] + 1,
                "lexicalCoverage": round(lexical_coverage, 4),
                "consonantCoverage": round(consonant_coverage, 4),
                "changedWordCount": changed,
            }
        )
    return extensions


def _extend_colead_across_speaker_transition(
    decoded: list[list[bool]],
    lexical_evidence: list[dict[str, Any]],
    lead_roles: list[str],
    backing_roles: list[str | None],
    seed_lines: list[bool],
    reference_groups: list[int],
    settings: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Continue a proven co-lead when the dominant singer changes mid-phrase.

    A duet can cause the separator's dominant lead stem to switch singers at a
    display-line boundary.  If the secondary stem remains the previous singer
    for the rest of that same semantic phrase, the switch is evidence of two
    simultaneous lyric owners, not a solo hand-off.  Exact word evidence wins
    over the display-line transition.  When no word run was decoded, the
    extension may begin only in the punctuation-delimited clause containing a
    strong same-lyric/distinct-speaker anchor; it never guesses an extra word
    on the preceding side of the transition.
    """
    if not (
        len(decoded)
        == len(lexical_evidence)
        == len(lead_roles)
        == len(backing_roles)
        == len(seed_lines)
        == len(reference_groups)
    ):
        raise ValueError("Co-lead transition evidence must have equal lengths")
    settings = settings or {}
    minimum_consonant_coverage = float(
        settings.get("minimumCoLeadWordConsonantCoverage", 0.6)
    )
    minimum_consonant_confidence = float(
        settings.get("minimumCoLeadMeanConsonantConfidence", 0.2)
    )
    minimum_consonant_ratio = float(
        settings.get("minimumCoLeadConsonantConfidenceRatio", 0.35)
    )

    def strong_distinct_speaker_anchor(word: dict[str, Any]) -> bool:
        lead_role = word.get("leadWordRole")
        backing_role = word.get("backingWordRole")
        lead_cluster = word.get("leadWordCluster")
        backing_cluster = word.get("backingWordCluster")
        distinct_speakers = bool(
            lead_role in {"male", "female"}
            and backing_role in {"male", "female"}
            and (
                lead_cluster != backing_cluster
                if isinstance(lead_cluster, int)
                and isinstance(backing_cluster, int)
                else lead_role != backing_role
            )
        )
        return bool(
            word.get("matched", False)
            and distinct_speakers
            and int(word.get("consonantCount", 0)) >= 1
            and float(word.get("consonantCoverage", 0.0))
            >= minimum_consonant_coverage
            and float(word.get("meanBackingConsonantConfidence", 0.0))
            >= minimum_consonant_confidence
            and float(word.get("backingToLeadConsonantConfidenceRatio", 0.0))
            >= minimum_consonant_ratio
        )

    extensions: list[dict[str, Any]] = []
    by_group: dict[int, list[int]] = {}
    for index, group in enumerate(reference_groups):
        by_group.setdefault(int(group), []).append(index)
    for group, indexes in by_group.items():
        for previous, current in zip(indexes, indexes[1:]):
            previous_role = lead_roles[previous]
            current_role = lead_roles[current]
            if (
                previous_role not in {"male", "female"}
                or current_role not in {"male", "female"}
                or previous_role == current_role
            ):
                continue
            tail = [index for index in indexes if index >= current]
            if (
                not tail
                or not any(seed_lines[index] for index in tail)
                or any(backing_roles[index] != previous_role for index in tail)
            ):
                continue
            existing = [
                (index, word_index)
                for index in indexes
                for word_index, value in enumerate(decoded[index])
                if value
            ]
            changed_word_count = 0
            if existing:
                start_line, start_word = existing[0]
                boundary_source = "existing-word-evidence"
            else:
                tail_words = [
                    (index, word_index, word)
                    for index in tail
                    for word_index, word in enumerate(
                        lexical_evidence[index].get("wordEvidence", [])
                    )
                ]
                anchor_position = next(
                    (
                        position
                        for position, (_, _, word) in enumerate(tail_words)
                        if strong_distinct_speaker_anchor(word)
                    ),
                    None,
                )
                if anchor_position is None:
                    continue
                punctuation_positions = [
                    position
                    for position, (_, _, word) in enumerate(
                        tail_words[:anchor_position]
                    )
                    if PUNCTUATION_BREAK.search(str(word.get("text", "")))
                ]
                start_position = (
                    punctuation_positions[-1] + 1
                    if punctuation_positions
                    else 0
                )
                start_line, start_word, _ = tail_words[start_position]
                boundary_source = (
                    "punctuation-before-word-anchor"
                    if punctuation_positions
                    else "transition-line-with-word-anchor"
                )
                for index, word_index, _ in tail_words[start_position:]:
                    if not decoded[index][word_index]:
                        decoded[index][word_index] = True
                        changed_word_count += 1
            start_words = lexical_evidence[start_line].get("wordEvidence", [])
            if start_word >= len(start_words):
                continue
            extensions.append(
                {
                    "referenceGroup": group,
                    "transitionAfterLine": previous + 1,
                    "fromRole": previous_role,
                    "toRole": current_role,
                    "startLine": start_line + 1,
                    "startWord": start_word + 1,
                    "startText": str(start_words[start_word].get("text", "")),
                    "endLine": tail[-1] + 1,
                    "boundarySource": boundary_source,
                    "changedWordCount": changed_word_count,
                }
            )
            break
    return extensions


def _restore_proven_speaker_transition_roles(
    smoothed_roles: list[str],
    raw_roles: list[str],
    extensions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Undo phrase smoothing only across acoustically proven singer changes.

    Semantic majority smoothing remains the default protection against isolated
    speaker-classification outliers.  A segment is restored to its raw roles
    only after the independent same-lyric co-lead decoder has established a
    real lead-speaker transition and its exact end boundary.
    """
    if len(smoothed_roles) != len(raw_roles):
        raise ValueError("Smoothed and raw speaker roles must have equal lengths")
    restored: list[dict[str, Any]] = []
    for extension in extensions:
        start = int(extension["transitionAfterLine"]) - 1
        end = int(extension["endLine"])
        if start < 0 or end > len(smoothed_roles) or start >= end:
            raise ValueError("Proven speaker transition has invalid line bounds")
        changed: list[int] = []
        for index in range(start, end):
            if smoothed_roles[index] == raw_roles[index]:
                continue
            smoothed_roles[index] = raw_roles[index]
            changed.append(index + 1)
        if changed:
            restored.append(
                {
                    "referenceGroup": int(extension["referenceGroup"]),
                    "changedLineIndexes": changed,
                    "evidence": "same-lyric-speaker-transition",
                }
            )
    return restored


def _reconcile_backing_roles_from_word_majority(
    line_roles: list[str | None],
    lexical_evidence: list[dict[str, Any]],
    settings: dict[str, Any],
) -> tuple[list[str | None], list[dict[str, Any]]]:
    """Refine a mixed line embedding with a stable majority of word embeddings."""
    if len(line_roles) != len(lexical_evidence):
        raise ValueError("Backing line and word speaker evidence must have equal lengths")
    minimum_words = int(settings.get("minimumSpeakerWordMajorityCount", 3))
    majority_ratio = float(
        settings.get(
            "speakerWordMajorityRatio",
            settings.get("speakerPhraseMajorityRatio", 0.66),
        )
    )
    reconciled = list(line_roles)
    overrides: list[dict[str, Any]] = []
    for index, (line_role, line) in enumerate(zip(line_roles, lexical_evidence)):
        word_roles = [
            str(word["backingWordRole"])
            for word in line.get("wordEvidence", [])
            if word.get("backingWordRole") in {"male", "female"}
        ]
        if len(word_roles) < minimum_words:
            continue
        counts = {
            role: word_roles.count(role) for role in ("male", "female")
        }
        winner = max(counts, key=counts.get)
        observed_ratio = counts[winner] / len(word_roles)
        if observed_ratio < majority_ratio or winner == line_role:
            continue
        reconciled[index] = winner
        overrides.append(
            {
                "line": index + 1,
                "lineRole": line_role,
                "wordMajorityRole": winner,
                "wordCount": len(word_roles),
                "majorityRatio": round(observed_ratio, 4),
            }
        )
    return reconciled, overrides


def split_lines_on_syllable_roles(
    lines: list[dict[str, Any]],
    lexical_evidence: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Split display lines at word-level vocal-role boundaries.

    Role inference is authoritative here. Presentation constraints such as a
    short cue lead must never recolor a solo word as duet; the render planner
    handles those constraints without changing the acoustic decision.
    """
    if len(lines) != len(lexical_evidence):
        raise ValueError("Word-level role evidence must match the lyric line count")
    solo_roles = [
        [str(line["role"])] * len(evidence.get("wordEvidence", []))
        for line, evidence in zip(lines, lexical_evidence)
    ]
    semantic_clause_carryovers: list[dict[str, Any]] = []
    for index in range(1, len(lines)):
        previous_line = lines[index - 1]
        current_line = lines[index]
        if (
            current_line.get("referenceGroup")
            != previous_line.get("referenceGroup")
        ):
            continue
        previous_words = lexical_evidence[index - 1].get("wordEvidence", [])
        current_words = lexical_evidence[index].get("wordEvidence", [])
        if (
            not previous_words
            or not current_words
            or PUNCTUATION_BREAK.search(
                str(previous_words[-1].get("text", ""))
            )
        ):
            continue
        punctuation_index = next(
            (
                word_index
                for word_index, word in enumerate(current_words)
                if PUNCTUATION_BREAK.search(str(word.get("text", "")))
            ),
            None,
        )
        if punctuation_index is None:
            continue
        prefix = current_words[: punctuation_index + 1]
        previous_role = str(previous_line.get("role", ""))
        current_role = str(current_line.get("role", ""))
        if (
            previous_role not in {"male", "female"}
            or current_role not in {"male", "female"}
            or previous_role == current_role
            or any(bool(word.get("coLead", False)) for word in prefix)
            or any(word.get("leadWordRole") != previous_role for word in prefix)
        ):
            continue
        for word_index in range(punctuation_index + 1):
            solo_roles[index][word_index] = previous_role
        semantic_clause_carryovers.append(
            {
                "referenceGroup": current_line.get("referenceGroup"),
                "fromLine": index,
                "toLine": index + 1,
                "throughWord": punctuation_index + 1,
                "throughText": str(prefix[-1].get("text", "")),
                "role": previous_role,
                "evidence": "unfinished-clause-plus-word-speaker",
            }
        )

    output: list[dict[str, Any]] = []
    boundary_count = 0
    for line_index, (line, evidence) in enumerate(zip(lines, lexical_evidence)):
        syllables = [dict(item) for item in line.get("syllables", [])]
        words = list(evidence.get("wordEvidence", []))
        if len(syllables) != len(words):
            raise ValueError("Word-level role evidence must match every lyric syllable")
        syllable_roles: list[str] = []
        for word_index, (syllable, word) in enumerate(zip(syllables, words)):
            role = (
                "duet"
                if bool(word.get("coLead", False))
                else str(
                    word.get(
                        "semanticClauseRole",
                        solo_roles[line_index][word_index],
                    )
                )
            )
            syllable["role"] = role
            syllable_roles.append(role)

        role_runs: list[tuple[str, int, int]] = []
        for word_index, role in enumerate(syllable_roles):
            if not role_runs or role_runs[-1][0] != role:
                role_runs.append((role, word_index, word_index))
            else:
                previous_role, start_index, _ = role_runs[-1]
                role_runs[-1] = (previous_role, start_index, word_index)

        groups: list[tuple[str, list[dict[str, Any]]]] = []
        for syllable, role in zip(syllables, syllable_roles):
            if not groups or groups[-1][0] != role:
                groups.append((role, [syllable]))
            else:
                groups[-1][1].append(syllable)
        boundary_count += max(0, len(groups) - 1)
        for role, group in groups:
            item = dict(line)
            item["start"] = float(group[0]["start"])
            item["end"] = float(group[-1]["end"])
            item["text"] = " ".join(str(word["text"]) for word in group)
            item["role"] = role
            item["roleEvidence"] = (
                "word-level-colead-sequence"
                if role == "duet"
                else str(line.get("roleEvidence", "speaker-lexical-inference"))
            )
            item["syllables"] = group
            output.append(item)
    for index, line in enumerate(output, start=1):
        line["index"] = index
        line["slot"] = "top" if index % 2 else "bottom"
    return output, {
        "lineCountBefore": len(lines),
        "lineCountAfter": len(output),
        "roleBoundarySplitCount": boundary_count,
        "cueRoleConsolidations": [],
        "semanticClauseCarryovers": semantic_clause_carryovers,
        "counts": {
            role: sum(str(line.get("role")) == role for line in output)
            for role in ("male", "female", "duet")
        },
    }


def select_backing_identity_candidates(
    lexical_evidence: list[dict[str, Any]], settings: dict[str, Any]
) -> list[int]:
    """Select secondary-stem lines that contain articulated authoritative lyrics.

    These observations may reveal a co-lead singer who never owns a solo line.
    Vowel-only harmony, ad-libs, and weak separator bleed are excluded by the
    consonant and confidence-ratio gates.
    """
    minimum_words = int(settings.get("minimumBackingIdentityCandidateWords", 2))
    minimum_coverage = float(settings.get("minimumCoLeadLexicalCoverage", 0.75))
    minimum_consonant_coverage = float(
        settings.get("minimumCoLeadConsonantCoverage", 0.6)
    )
    minimum_consonant_confidence = float(
        settings.get("minimumCoLeadMeanConsonantConfidence", 0.2)
    )
    minimum_consonant_ratio = float(
        settings.get("minimumCoLeadConsonantConfidenceRatio", 0.35)
    )
    selected: list[int] = []
    for index, evidence in enumerate(lexical_evidence):
        word_count = int(evidence.get("wordCount", 0))
        consonant_count = int(evidence.get("consonantCount", 0))
        if word_count < minimum_words or consonant_count < 1:
            continue
        if int(evidence.get("matchedWordCount", 0)) / word_count < minimum_coverage:
            continue
        if (
            int(evidence.get("supportedConsonantCount", 0)) / consonant_count
            < minimum_consonant_coverage
        ):
            continue
        if (
            float(evidence.get("meanBackingConsonantConfidence", 0.0))
            < minimum_consonant_confidence
        ):
            continue
        if (
            float(evidence.get("backingToLeadConsonantConfidenceRatio", 0.0))
            < minimum_consonant_ratio
        ):
            continue
        selected.append(index)
    return selected


def smooth_roles_with_semantic_group_embeddings(
    roles: list[str],
    reference_groups: list[int],
    group_roles: dict[int, str | None],
    group_diagnostics: dict[int, dict[str, Any]],
) -> tuple[list[str], list[dict[str, Any]]]:
    """Resolve display-split outliers using one embedding over the full phrase."""
    if len(roles) != len(reference_groups):
        raise ValueError("Semantic-group smoothing evidence must match line roles")
    resolved = list(roles)
    by_group: dict[int, list[int]] = {}
    for index, group in enumerate(reference_groups):
        by_group.setdefault(int(group), []).append(index)
    report: list[dict[str, Any]] = []
    for group, indexes in by_group.items():
        observed = sorted({resolved[index] for index in indexes})
        if len(indexes) < 2 or len(observed) < 2:
            continue
        group_role = group_roles.get(group)
        diagnostics = group_diagnostics.get(group, {})
        if group_role not in {"male", "female"}:
            report.append(
                {
                    "referenceGroup": group,
                    "status": "unresolved",
                    "observedRoles": observed,
                    "diagnostics": diagnostics,
                }
            )
            continue
        changed = [index for index in indexes if resolved[index] != group_role]
        for index in changed:
            resolved[index] = group_role
        report.append(
            {
                "referenceGroup": group,
                "status": "resolved",
                "role": group_role,
                "changedLineIndexes": [index + 1 for index in changed],
                "diagnostics": diagnostics,
            }
        )
    return resolved, report


def resolve_mixed_group_lines_from_unanimous_clause_roles(
    line_roles: list[str],
    reference_groups: list[int],
    clauses: list[dict[str, Any]],
    clause_roles: list[str | None],
    *,
    proven_transition_groups: set[int],
) -> tuple[list[str], list[dict[str, Any]]]:
    """Prefer complete semantic-clause evidence over arbitrary display splits."""
    if len(line_roles) != len(reference_groups) or len(clauses) != len(clause_roles):
        raise ValueError("Semantic role consistency evidence has inconsistent lengths")
    resolved = list(line_roles)
    by_group: dict[int, list[int]] = {}
    for index, group in enumerate(reference_groups):
        by_group.setdefault(int(group), []).append(index)
    report: list[dict[str, Any]] = []
    for group, line_indexes in by_group.items():
        observed = sorted({resolved[index] for index in line_indexes})
        if len(observed) < 2 or group in proven_transition_groups:
            continue
        clause_indexes = [
            index
            for index, clause in enumerate(clauses)
            if int(clause["referenceGroup"]) == group
        ]
        if not clause_indexes:
            continue
        covered_lines: set[int] = set()
        valid_coverage = True
        for clause_index in clause_indexes:
            for reference in clauses[clause_index].get("wordReferences", []):
                line_index = int(reference.get("line", 0)) - 1
                if (
                    line_index < 0
                    or line_index >= len(reference_groups)
                    or int(reference_groups[line_index]) != group
                ):
                    valid_coverage = False
                    break
                covered_lines.add(line_index)
            if not valid_coverage:
                break
        candidate_roles = {clause_roles[index] for index in clause_indexes}
        if (
            not valid_coverage
            or covered_lines != set(line_indexes)
            or len(candidate_roles) != 1
            or not candidate_roles <= {"male", "female"}
        ):
            continue
        role = next(iter(candidate_roles))
        changed = [index for index in line_indexes if resolved[index] != role]
        for index in changed:
            resolved[index] = role
        report.append(
            {
                "referenceGroup": group,
                "role": role,
                "previousRoles": observed,
                "lineIndexes": [index + 1 for index in line_indexes],
                "clauseIndexes": [index + 1 for index in clause_indexes],
                "changedLineIndexes": [index + 1 for index in changed],
                "evidence": "unanimous-full-coverage-semantic-clause-roles",
            }
        )
    return resolved, report


def build_semantic_clause_segments(
    lines: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[list[int]]]:
    """Aggregate arbitrary display lines into punctuation-delimited clauses."""
    mapping = [[-1] * len(line.get("syllables", [])) for line in lines]
    clauses: list[dict[str, Any]] = []
    current_group: int | None = None
    current_words: list[tuple[int, int, dict[str, Any]]] = []

    def flush() -> None:
        nonlocal current_words
        if not current_words:
            return
        clause_index = len(clauses)
        for line_index, word_index, _ in current_words:
            mapping[line_index][word_index] = clause_index
        clauses.append(
            {
                "referenceGroup": current_group,
                "start": float(current_words[0][2]["start"]),
                "end": float(current_words[-1][2]["end"]),
                "text": " ".join(
                    str(word.get("text", "")) for _, _, word in current_words
                ),
                "wordCount": len(current_words),
                "wordReferences": [
                    {"line": line_index + 1, "word": word_index + 1}
                    for line_index, word_index, _ in current_words
                ],
            }
        )
        current_words = []

    for line_index, line in enumerate(lines):
        group = int(line.get("referenceGroup", line_index + 1))
        if current_group is not None and group != current_group:
            flush()
        current_group = group
        for word_index, word in enumerate(line.get("syllables", [])):
            current_words.append((line_index, word_index, word))
            if PUNCTUATION_BREAK.search(str(word.get("text", ""))):
                flush()
    flush()
    if any(index < 0 for line in mapping for index in line):
        raise RuntimeError("Semantic clause mapping did not consume every lyric word")
    return clauses, mapping


def resolve_ambiguous_semantic_clause_roles_from_group_embeddings(
    roles: list[str | None],
    clauses: list[dict[str, Any]],
    group_roles: dict[int, str | None],
    group_diagnostics: dict[int, dict[str, Any]],
) -> tuple[list[str | None], list[dict[str, Any]]]:
    """Use only an accepted embedding for the same authoritative lyric group."""
    if len(roles) != len(clauses):
        raise ValueError("Semantic clause aggregate evidence must match clause roles")
    resolved = list(roles)
    report: list[dict[str, Any]] = []
    for index, (role, clause) in enumerate(zip(resolved, clauses)):
        if role in {"male", "female"}:
            continue
        group = int(clause["referenceGroup"])
        candidate = group_roles.get(group)
        diagnostics = group_diagnostics.get(group, {})
        if candidate not in {"male", "female"} or diagnostics.get("role") != candidate:
            continue
        resolved[index] = candidate
        report.append(
            {
                "clause": index + 1,
                "referenceGroup": group,
                "role": candidate,
                "cosineMargin": diagnostics.get("cosineMargin"),
                "evidence": "accepted-authoritative-group-voiceprint",
            }
        )
    return resolved, report


def resolve_ambiguous_semantic_group_roles_from_pitch_consensus(
    group_roles: dict[int, str | None],
    group_diagnostics: dict[int, dict[str, Any]],
    line_pitch_medians: list[float | None],
    reference_groups: list[int],
    cluster_state: dict[str, Any],
    settings: dict[str, Any],
) -> tuple[dict[int, str | None], dict[int, dict[str, Any]], list[dict[str, Any]]]:
    """Accept a full-group role only when aggregate pitch and cluster candidate agree."""
    if len(line_pitch_medians) != len(reference_groups):
        raise ValueError("Semantic-group pitch evidence must match line groups")
    resolved_roles = dict(group_roles)
    resolved_diagnostics = {
        group: dict(diagnostics) for group, diagnostics in group_diagnostics.items()
    }
    male_maximum = float(settings.get("maleMaximumMedianHz", 235.0))
    female_minimum = float(settings.get("femaleMinimumMedianHz", 275.0))
    minimum_ratio = float(settings.get("minimumSemanticGroupPitchConsensusRatio", 0.5))
    if not 0 < minimum_ratio <= 1:
        raise ValueError("Semantic-group pitch consensus ratio must be in (0, 1]")

    def pitch_role(value: float) -> str | None:
        if value <= male_maximum:
            return "male"
        if value >= female_minimum:
            return "female"
        return None

    report: list[dict[str, Any]] = []
    for group, diagnostics in resolved_diagnostics.items():
        if resolved_roles.get(group) in {"male", "female"}:
            continue
        indexes = [
            index
            for index, candidate in enumerate(reference_groups)
            if int(candidate) == int(group)
        ]
        if len(indexes) < 2:
            continue
        pitches = [
            float(line_pitch_medians[index])
            for index in indexes
            if line_pitch_medians[index] is not None
            and math.isfinite(float(line_pitch_medians[index]))
        ]
        classified_roles = [role for pitch in pitches if (role := pitch_role(pitch))]
        classified = set(classified_roles)
        ratio = len(classified_roles) / max(1, len(indexes))
        if not pitches or len(classified) != 1 or ratio < minimum_ratio:
            continue
        aggregate_pitch = float(statistics.median(pitches))
        aggregate_role = pitch_role(aggregate_pitch)
        cluster = diagnostics.get("cluster")
        candidate_role = (
            cluster_state.get("roleByCluster", {}).get(int(cluster))
            if isinstance(cluster, int)
            else None
        )
        role = next(iter(classified))
        if aggregate_role != role or candidate_role != role:
            continue
        resolved_roles[group] = role
        diagnostics["role"] = role
        diagnostics["resolutionEvidence"] = "group-pitch-confirmed-cluster-candidate"
        diagnostics["aggregatePitchHz"] = round(aggregate_pitch, 2)
        diagnostics["classifiedPitchLineRatio"] = round(ratio, 4)
        report.append(
            {
                "referenceGroup": int(group),
                "role": role,
                "aggregatePitchHz": round(aggregate_pitch, 2),
                "linePitchMediansHz": [round(pitch, 2) for pitch in pitches],
                "classifiedPitchLineRatio": round(ratio, 4),
                "minimumClassifiedPitchLineRatio": minimum_ratio,
                "cluster": cluster,
                "embeddingCosineMargin": diagnostics.get("cosineMargin"),
                "evidence": "group-pitch-confirmed-cluster-candidate",
            }
        )
    return resolved_roles, resolved_diagnostics, report


def resolve_ambiguous_semantic_clause_roles_from_line_consensus(
    roles: list[str | None],
    clauses: list[dict[str, Any]],
    raw_line_roles: list[str],
    line_diagnostics: list[dict[str, Any]],
    *,
    minimum_margin: float,
) -> tuple[list[str | None], list[dict[str, Any]]]:
    """Use unanimous confident lines containing every exact clause word."""
    if len(roles) != len(clauses) or len(raw_line_roles) != len(line_diagnostics):
        raise ValueError("Semantic clause line evidence has inconsistent lengths")
    if not math.isfinite(minimum_margin) or minimum_margin < 0:
        raise ValueError("Semantic clause line consensus margin must be non-negative")
    resolved = list(roles)
    report: list[dict[str, Any]] = []
    for index, (role, clause) in enumerate(zip(resolved, clauses)):
        if role in {"male", "female"}:
            continue
        line_indexes = sorted(
            {
                int(reference.get("line", 0)) - 1
                for reference in clause.get("wordReferences", [])
            }
        )
        if not line_indexes or any(
            line_index < 0 or line_index >= len(raw_line_roles)
            for line_index in line_indexes
        ):
            continue
        observations = [
            (raw_line_roles[line_index], line_diagnostics[line_index])
            for line_index in line_indexes
        ]
        candidates = {candidate for candidate, _ in observations}
        margins = [
            float(diagnostics.get("cosineMargin", -math.inf))
            for _, diagnostics in observations
        ]
        if (
            len(candidates) != 1
            or not candidates <= {"male", "female"}
            or not all(
                diagnostics.get("rawRole") == candidate
                for candidate, diagnostics in observations
            )
            or any(not math.isfinite(margin) or margin < minimum_margin for margin in margins)
        ):
            continue
        candidate = next(iter(candidates))
        resolved[index] = candidate
        report.append(
            {
                "clause": index + 1,
                "referenceGroup": int(clause["referenceGroup"]),
                "role": candidate,
                "lineIndexes": [line_index + 1 for line_index in line_indexes],
                "minimumCosineMargin": round(min(margins), 4),
                "requiredCosineMargin": round(minimum_margin, 4),
                "evidence": "unanimous-confident-containing-line-voiceprints",
            }
        )
    return resolved, report


ROLE_INFERENCE_CHECKPOINTS = (
    ("line-identity", 15.0, "Measured lead-line pitch and speaker identity"),
    ("lexical-evidence", 35.0, "Aligned lead, backing and gender evidence to exact words"),
    ("speaker-clusters", 45.0, "Established singer identity clusters"),
    ("clause-evidence", 55.0, "Measured semantic-clause speaker evidence"),
    ("group-evidence", 65.0, "Resolved authoritative-group speaker evidence"),
    ("word-identity", 80.0, "Measured word-level lead and backing identity"),
    ("colead-decoding", 95.0, "Decoded independently supported co-lead words"),
    ("complete", 100.0, "Completed automatic vocal-role inference"),
)


def _report_role_inference_checkpoint(
    on_progress: Callable[[float, str], None] | None,
    key: str,
) -> None:
    if on_progress is None:
        return
    for candidate, percent, message in ROLE_INFERENCE_CHECKPOINTS:
        if candidate == key:
            on_progress(percent, message)
            return
    raise ValueError(f"Unknown role inference checkpoint: {key}")


def infer_automatic_vocal_roles(
    root: Path,
    lead_path: Path,
    backing_path: Path,
    gender_male_path: Path,
    gender_female_path: Path,
    lines: list[dict[str, Any]],
    settings: dict[str, Any],
    *,
    on_progress: Callable[[float, str], None] | None = None,
) -> tuple[list[str], dict[str, Any]]:
    """Infer male/female/duet from timbre identity plus same-lyric evidence."""
    import numpy as np

    pitch_medians = _line_pitch_medians(lead_path, lines, settings)
    lead_embeddings, backing_embeddings = _speaker_line_embeddings(
        root, [lead_path, backing_path], lines, settings
    )
    _report_role_inference_checkpoint(on_progress, "line-identity")
    groups = [int(line.get("referenceGroup", index + 1)) for index, line in enumerate(lines)]
    line_alignment_settings = dict(settings)
    line_alignment_settings["coLeadSemanticGroupAlignment"] = False
    lexical = _colead_lexical_evidence(
        root, lead_path, backing_path, lines, line_alignment_settings
    )
    opposite_gender_lexical = _colead_lexical_evidence(
        root,
        gender_male_path,
        gender_female_path,
        lines,
        line_alignment_settings,
    )
    phrase_alignment_settings = dict(settings)
    phrase_alignment_settings["coLeadSemanticGroupAlignment"] = True
    phrase_lexical = _colead_lexical_evidence(
        root, lead_path, backing_path, lines, phrase_alignment_settings
    )
    _report_role_inference_checkpoint(on_progress, "lexical-evidence")
    backing_identity_indexes = select_backing_identity_candidates(lexical, settings)
    identity_embeddings = np.asarray(lead_embeddings, dtype=np.float32)
    identity_pitches = list(pitch_medians)
    # Candidate groups are unique so secondary observations contribute identity
    # evidence without voting in the lead-line semantic smoothing pass.
    identity_groups = list(groups)
    if backing_identity_indexes:
        backing_pitches = _line_pitch_medians(backing_path, lines, settings)
        identity_embeddings = np.concatenate(
            (
                identity_embeddings,
                np.asarray(backing_embeddings, dtype=np.float32)[backing_identity_indexes],
            ),
            axis=0,
        )
        identity_pitches.extend(backing_pitches[index] for index in backing_identity_indexes)
        group_base = max(groups, default=0) + 1
        identity_groups.extend(
            group_base + offset for offset in range(len(backing_identity_indexes))
        )
    identity_roles, speaker_report, cluster_state = cluster_speaker_embeddings(
        identity_embeddings, identity_pitches, identity_groups, settings
    )
    _report_role_inference_checkpoint(on_progress, "speaker-clusters")
    lead_roles = identity_roles[: len(lines)]
    raw_lead_roles = [
        str(item.get("rawRole", item["role"]))
        for item in speaker_report["lines"][: len(lines)]
    ]
    for index, item in enumerate(speaker_report["lines"]):
        if index < len(lines):
            item["observation"] = "lead-line"
            item["sourceLine"] = index + 1
        else:
            source_index = backing_identity_indexes[index - len(lines)]
            item["observation"] = "articulated-backing-line"
            item["sourceLine"] = source_index + 1
    speaker_report["backingIdentityCandidates"] = [
        index + 1 for index in backing_identity_indexes
    ]
    semantic_clauses, semantic_clause_mapping = build_semantic_clause_segments(lines)
    semantic_clause_embeddings = _speaker_segment_embeddings(
        root,
        [lead_path],
        semantic_clauses,
        settings,
        minimum_segment_seconds=float(
            settings.get("minimumSemanticClauseSegmentSeconds", 0.75)
        ),
    )[0]
    semantic_clause_settings = dict(settings)
    semantic_clause_settings["minimumBackingSpeakerMargin"] = float(
        settings.get("minimumSemanticClauseSpeakerMargin", 0.12)
    )
    semantic_clause_roles, semantic_clause_diagnostics = assign_speaker_embeddings(
        semantic_clause_embeddings, cluster_state, semantic_clause_settings
    )
    _report_role_inference_checkpoint(on_progress, "clause-evidence")
    group_order = list(dict.fromkeys(groups))
    group_segments: list[dict[str, float]] = []
    for group in group_order:
        indexes = [index for index, value in enumerate(groups) if value == group]
        group_segments.append(
            {
                "start": float(lines[indexes[0]]["start"]),
                "end": float(lines[indexes[-1]]["end"]),
            }
        )
    semantic_group_embeddings = _speaker_segment_embeddings(
        root, [lead_path], group_segments, settings
    )[0]
    semantic_group_settings = dict(settings)
    semantic_group_settings["minimumBackingSpeakerMargin"] = float(
        settings.get("minimumSemanticGroupSpeakerMargin", 0.1)
    )
    semantic_group_roles_list, semantic_group_diagnostics_list = (
        assign_speaker_embeddings(
            semantic_group_embeddings, cluster_state, semantic_group_settings
        )
    )
    semantic_group_roles = dict(zip(group_order, semantic_group_roles_list))
    semantic_group_diagnostics = dict(
        zip(group_order, semantic_group_diagnostics_list)
    )
    (
        semantic_group_roles,
        semantic_group_diagnostics,
        semantic_group_pitch_resolutions,
    ) = resolve_ambiguous_semantic_group_roles_from_pitch_consensus(
        semantic_group_roles,
        semantic_group_diagnostics,
        pitch_medians,
        groups,
        cluster_state,
        settings,
    )
    semantic_clause_roles, semantic_clause_aggregate_resolutions = (
        resolve_ambiguous_semantic_clause_roles_from_group_embeddings(
            semantic_clause_roles,
            semantic_clauses,
            semantic_group_roles,
            semantic_group_diagnostics,
        )
    )
    semantic_clause_roles, semantic_clause_line_resolutions = (
        resolve_ambiguous_semantic_clause_roles_from_line_consensus(
            semantic_clause_roles,
            semantic_clauses,
            raw_lead_roles,
            speaker_report["lines"][: len(lines)],
            minimum_margin=float(
                settings.get("minimumSemanticClauseLineConsensusMargin", 0.05)
            ),
        )
    )
    _report_role_inference_checkpoint(on_progress, "group-evidence")
    ambiguous_clauses = [
        {
            **semantic_clauses[index],
            "diagnostics": semantic_clause_diagnostics[index],
        }
        for index, role in enumerate(semantic_clause_roles)
        if role not in {"male", "female"}
    ]
    if ambiguous_clauses:
        raise ValueError(
            "Ambiguous lead singer for a semantic lyric clause: "
            + json.dumps(ambiguous_clauses, ensure_ascii=False)
        )
    if any(role not in {"male", "female"} for role in semantic_clause_roles):
        raise RuntimeError("Semantic clause role validation failed before word projection")
    for line_index, clause_indexes in enumerate(semantic_clause_mapping):
        words = lexical[line_index].get("wordEvidence", [])
        if len(words) != len(clause_indexes):
            raise RuntimeError(
                "Semantic clause speaker evidence must match every lyric word"
            )
        for word, clause_index in zip(words, clause_indexes):
            word["semanticClauseRole"] = semantic_clause_roles[clause_index]
            word["semanticClauseIndex"] = clause_index + 1
    speaker_report["semanticClauseAssignments"] = [
        {
            **clause,
            "role": semantic_clause_roles[index],
            "diagnostics": semantic_clause_diagnostics[index],
        }
        for index, clause in enumerate(semantic_clauses)
    ]
    speaker_report["semanticClauseAggregateResolutions"] = (
        semantic_clause_aggregate_resolutions
    )
    speaker_report["semanticGroupPitchResolutions"] = semantic_group_pitch_resolutions
    speaker_report["semanticClauseLineConsensusResolutions"] = (
        semantic_clause_line_resolutions
    )
    speaker_report["ambiguousSemanticClauses"] = ambiguous_clauses
    lead_roles, semantic_group_smoothing = (
        smooth_roles_with_semantic_group_embeddings(
            lead_roles,
            groups,
            semantic_group_roles,
            semantic_group_diagnostics,
        )
    )
    speaker_report["semanticGroupEmbeddingSmoothing"] = semantic_group_smoothing
    backing_roles, backing_report = assign_speaker_embeddings(
        backing_embeddings, cluster_state, settings
    )
    for index, (line_evidence, phrase_evidence) in enumerate(
        zip(lexical, phrase_lexical)
    ):
        lead_cluster = int(speaker_report["lines"][index]["cluster"])
        backing_cluster = int(backing_report[index]["cluster"])
        for evidence in (line_evidence, phrase_evidence):
            evidence["leadSpeakerCluster"] = lead_cluster
            evidence["backingSpeakerCluster"] = backing_cluster
    word_segments = [
        syllable
        for line in lines
        for syllable in line.get("syllables", [])
    ]
    word_lead_embeddings, word_backing_embeddings = _speaker_segment_embeddings(
        root,
        [lead_path, backing_path],
        word_segments,
        settings,
        context_seconds=float(settings.get("speakerWordContextSeconds", 0.05)),
        minimum_segment_seconds=float(
            settings.get("speakerWordMinimumSegmentSeconds", 0.45)
        ),
    )
    word_settings = dict(settings)
    word_settings["minimumBackingSpeakerMargin"] = float(
        settings.get("minimumWordSpeakerMargin", 0.0)
    )
    word_lead_roles, word_lead_report = assign_speaker_embeddings(
        word_lead_embeddings, cluster_state, word_settings
    )
    word_backing_roles, word_backing_report = assign_speaker_embeddings(
        word_backing_embeddings, cluster_state, word_settings
    )
    _report_role_inference_checkpoint(on_progress, "word-identity")
    cursor = 0
    for line in lexical:
        for word in line.get("wordEvidence", []):
            word["leadWordRole"] = word_lead_roles[cursor]
            word["backingWordRole"] = word_backing_roles[cursor]
            word["leadWordCluster"] = word_lead_report[cursor]["cluster"]
            word["backingWordCluster"] = word_backing_report[cursor]["cluster"]
            word["leadSpeakerMargin"] = word_lead_report[cursor]["cosineMargin"]
            word["backingSpeakerMargin"] = word_backing_report[cursor]["cosineMargin"]
            cursor += 1
    if cursor != len(word_segments):
        raise RuntimeError("Word speaker evidence did not consume every lyric syllable")
    colead_backing_roles, backing_role_reconciliation = (
        _reconcile_backing_roles_from_word_majority(
            backing_roles, lexical, settings
        )
    )
    roles, colead_report = decide_colead_roles(
        raw_lead_roles,
        colead_backing_roles,
        phrase_lexical,
        settings,
        reference_groups=groups,
    )
    seed_lines = [role == "duet" for role in roles]
    decoded_word_roles, word_colead_report = decide_colead_word_roles(
        lexical,
        settings,
        seed_lines=seed_lines,
        reference_groups=groups,
    )
    word_colead_report["seededSemanticGroupExtensions"] = (
        _extend_seeded_colead_semantic_groups(
            decoded_word_roles,
            phrase_lexical,
            raw_lead_roles,
            colead_backing_roles,
            seed_lines,
            groups,
            settings,
        )
    )
    word_colead_report["duetWordCountBeforeForegroundGate"] = sum(
        sum(line) for line in decoded_word_roles
    )
    word_colead_report["foregroundProminence"] = (
        gate_colead_groups_by_foreground_prominence(
            decoded_word_roles,
            lexical,
            groups,
            settings,
            foreground_verifiable_unseeded_ranges=word_colead_report.get(
                "foregroundVerifiableUnseededRanges", []
            ),
        )
    )
    word_colead_report["sungClauseRoleRegularization"] = (
        regularize_colead_to_sung_clause_boundaries(
            decoded_word_roles,
            lines,
            semantic_clause_mapping,
            settings,
        )
    )
    word_colead_report["oppositeGenderClauseConsensus"] = (
        promote_opposite_gender_colead_clauses(
            decoded_word_roles,
            opposite_gender_lexical,
            semantic_clause_mapping,
            settings,
        )
    )
    word_colead_report["duetWordCount"] = sum(
        sum(line) for line in decoded_word_roles
    )
    effective_seed_lines = [any(line) for line in decoded_word_roles]
    transition_extensions = _extend_colead_across_speaker_transition(
        decoded_word_roles,
        lexical,
        raw_lead_roles,
        colead_backing_roles,
        effective_seed_lines,
        groups,
        settings,
    )
    word_colead_report["speakerTransitionExtensions"] = transition_extensions
    speaker_report["provenTransitionSmoothingRestorations"] = (
        _restore_proven_speaker_transition_roles(
            lead_roles, raw_lead_roles, transition_extensions
        )
    )
    proven_transition_groups = {
        int(item["referenceGroup"]) for item in transition_extensions
    }
    lead_roles, semantic_clause_consistency_resolutions = (
        resolve_mixed_group_lines_from_unanimous_clause_roles(
            lead_roles,
            groups,
            semantic_clauses,
            semantic_clause_roles,
            proven_transition_groups=proven_transition_groups,
        )
    )
    unresolved_semantic_role_changes: list[dict[str, Any]] = []
    by_group: dict[int, list[int]] = {}
    for index, group in enumerate(groups):
        by_group.setdefault(group, []).append(index)
    for group, indexes in by_group.items():
        observed = sorted({lead_roles[index] for index in indexes})
        if len(observed) > 1 and group not in proven_transition_groups:
            unresolved_semantic_role_changes.append(
                {
                    "referenceGroup": group,
                    "lineIndexes": [index + 1 for index in indexes],
                    "roles": observed,
                    "evidence": [
                        {
                            "line": index + 1,
                            "lineRole": lead_roles[index],
                            "rawLineRole": raw_lead_roles[index],
                            "speaker": speaker_report["lines"][index],
                            "leadWordRoleCounts": {
                                role: sum(
                                    word.get("leadWordRole") == role
                                    for word in lexical[index].get("wordEvidence", [])
                                )
                                for role in ("male", "female")
                            },
                            "backingWordRoleCounts": {
                                role: sum(
                                    word.get("backingWordRole") == role
                                    for word in lexical[index].get("wordEvidence", [])
                                )
                                for role in ("male", "female")
                            },
                            "coLeadSeed": seed_lines[index],
                            "decodedCoLeadWordCount": sum(
                                decoded_word_roles[index]
                            ),
                        }
                        for index in indexes
                    ],
                }
            )
    fail_on_semantic_change = bool(
        settings.get("failOnInconsistentSemanticGroupRoles", True)
    )
    speaker_report["semanticRoleConsistency"] = {
        "status": (
            "failed"
            if unresolved_semantic_role_changes and fail_on_semantic_change
            else "best-evidence-with-warnings"
            if unresolved_semantic_role_changes
            else "passed"
        ),
        "selectionPolicy": "highest-line-and-word-speaker-evidence",
        "unresolvedChanges": unresolved_semantic_role_changes,
        "clauseResolutions": semantic_clause_consistency_resolutions,
        "provenTransitionGroups": sorted(proven_transition_groups),
    }
    if fail_on_semantic_change and unresolved_semantic_role_changes:
        raise ValueError(
            "Unresolved speaker change inside a semantic lyric group: "
            + json.dumps(unresolved_semantic_role_changes, ensure_ascii=False)
        )
    if (
        bool(settings.get("failOnAmbiguousCoLeadSemanticTail", True))
        and word_colead_report["ambiguousSemanticTails"]
    ):
        raise ValueError(
            "Ambiguous co-lead boundary at a semantic phrase tail: "
            + json.dumps(
                word_colead_report["ambiguousSemanticTails"], ensure_ascii=False
            )
        )
    _report_role_inference_checkpoint(on_progress, "colead-decoding")
    for line, decoded in zip(lexical, decoded_word_roles):
        for word, is_colead in zip(line.get("wordEvidence", []), decoded):
            word["coLead"] = bool(is_colead)
            word["assignedRole"] = (
                "duet" if is_colead else word.get("leadWordRole")
            )
    for index, item in enumerate(lexical):
        item["leadRole"] = lead_roles[index]
        item["backingRole"] = colead_backing_roles[index]
        item["backingLineRole"] = backing_roles[index]
        item["assignedRole"] = roles[index]
    # Line-level co-lead candidates remain diagnostic. The actual duet color is
    # applied only after the word-sequence decoder establishes exact boundaries.
    _report_role_inference_checkpoint(on_progress, "complete")
    return lead_roles, {
        "schemaVersion": 1,
        "status": "passed",
        "semanticPolicy": "lead-owner-with-word-sequence-colead-boundaries",
        "speakerEmbeddingModel": str(
            settings.get("speakerEmbeddingModel", "microsoft/wavlm-base-plus-sv")
        ),
        "speakerClustering": speaker_report,
        "backingSpeakerAssignments": backing_report,
        "coLeadBackingRoleReconciliation": backing_role_reconciliation,
        "wordLeadSpeakerAssignments": word_lead_report,
        "wordBackingSpeakerAssignments": word_backing_report,
        "coLead": colead_report,
        "wordCoLead": word_colead_report,
        "oppositeGenderLineEvidence": opposite_gender_lexical,
        "semanticGroupLineEvidence": phrase_lexical,
        "lineEvidence": lexical,
    }


def assign_lead_roles(
    lines: list[dict[str, Any]],
    directives: dict[str, Any],
    settings: dict[str, Any],
    *,
    automatic_roles: list[str | None] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Assign lyric colors from the owner of the displayed lyric.

    A backing harmony, ad-lib, or non-lexical vocal never promotes a line to
    duet. Duet is deliberately fail-closed and requires explicit co-lead
    evidence because a mixed vocal stem cannot establish that two singers are
    performing the displayed words rather than singing unrelated backing parts.
    """
    role_directives = directives.get("roles", {})
    semantic_policy = str(
        role_directives.get(
            "semanticPolicy", settings.get("semanticPolicy", "lead-lyric-owner")
        )
    ).strip()
    if semantic_policy != "lead-lyric-owner":
        raise ValueError(f"Unsupported vocal role semantic policy: {semantic_policy}")
    default_role = str(
        role_directives.get(
            "defaultLeadRole",
            role_directives.get(
                "defaultRole",
                settings.get("defaultLeadRole", settings.get("defaultRole", "male")),
            ),
        )
    ).lower()
    allowed = {"male", "female", "duet"}
    if default_role not in allowed:
        raise ValueError(f"Invalid default lead vocal role: {default_role}")
    authoritative = bool(role_directives.get("authoritative", False))
    raw_ranges = role_directives.get("ranges", [])
    ranges: list[dict[str, Any]] = []
    for index, item in enumerate(raw_ranges, start=1):
        start = float(item["startSeconds"])
        end = float(item["endSeconds"])
        if end <= start:
            raise ValueError(f"Vocal role range {index} has a non-positive duration")
        role = str(item.get("leadRole", item.get("role", ""))).lower()
        if role not in allowed:
            raise ValueError(f"Invalid lead vocal role in sidecar: {role}")
        if (
            role == "duet"
            and bool(settings.get("duetRequiresCoLeadEvidence", True))
            and not bool(item.get("coLead", False))
        ):
            raise ValueError(
                "Duet role requires coLead=true; backing vocals and ad-libs "
                "must retain the displayed lyric owner's male/female role"
            )
        ranges.append({"start": start, "end": end, "role": role})
    ranges.sort(key=lambda item: (item["start"], item["end"]))
    for previous, current in zip(ranges, ranges[1:]):
        if current["start"] < previous["end"]:
            raise ValueError("Vocal lead-role ranges must not overlap")

    if automatic_roles is not None and len(automatic_roles) != len(lines):
        raise ValueError("Automatic role evidence must match the lyric line count")
    output = [dict(line) for line in lines]
    counts = {"male": 0, "female": 0, "duet": 0}
    evidence_counts = {
        "lead-annotation": 0,
        "speaker-lexical-inference": 0,
        "pitch-assistance": 0,
        "default": 0,
    }
    previous_role = default_role
    for index, line in enumerate(output):
        midpoint = (float(line["start"]) + float(line["end"])) / 2
        matching = next(
            (
                item
                for item in ranges
                if item["start"] <= midpoint < item["end"]
            ),
            None,
        )
        if matching is not None:
            role = str(matching["role"])
            evidence = "lead-annotation"
        elif not authoritative and automatic_roles is not None and automatic_roles[index] in allowed:
            role = str(automatic_roles[index])
            evidence = (
                "speaker-lexical-inference"
                if bool(settings.get("speakerDiarization", False))
                else "pitch-assistance"
            )
        else:
            role = (
                previous_role
                if not authoritative and automatic_roles is not None
                else default_role
            )
            evidence = "default"
        line["role"] = role
        line["roleEvidence"] = evidence
        counts[role] += 1
        evidence_counts[evidence] += 1
        previous_role = role
    return output, {
        "mode": "lead-lyric-owner",
        "authoritative": authoritative,
        "duetRequiresCoLeadEvidence": bool(
            settings.get("duetRequiresCoLeadEvidence", True)
        ),
        "backingVocalsAffectRole": False,
        "counts": counts,
        "evidenceCounts": evidence_counts,
    }


def _pitch_assisted_lead_roles(
    vocal_path: Path,
    lines: list[dict[str, Any]],
    settings: dict[str, Any],
) -> tuple[list[str | None], list[dict[str, Any]]]:
    """Estimate only the dominant male/female lead; never infer duet."""
    import librosa
    import numpy as np

    waveform, sample_rate = librosa.load(vocal_path, sr=16_000, mono=True)
    male_maximum = float(settings.get("maleMaximumMedianHz", 235.0))
    female_minimum = float(settings.get("femaleMinimumMedianHz", 275.0))
    minimum_frames = int(settings.get("minimumVoicedFrames", 8))
    minimum_probability = float(settings.get("minimumVoicedProbability", 0.55))
    roles: list[str | None] = []
    diagnostics: list[dict[str, Any]] = []
    for line in lines:
        start = max(0, round(float(line["start"]) * sample_rate))
        end = min(len(waveform), round(float(line["end"]) * sample_rate))
        clip = waveform[start:end]
        role: str | None = None
        median_hz: float | None = None
        voiced_count = 0
        if len(clip) >= 2_048:
            f0, voiced, probabilities = librosa.pyin(
                clip,
                fmin=75.0,
                fmax=600.0,
                sr=sample_rate,
                frame_length=2_048,
                hop_length=320,
            )
            usable = (
                voiced
                & np.isfinite(f0)
                & np.isfinite(probabilities)
                & (probabilities >= minimum_probability)
            )
            voiced_count = int(np.count_nonzero(usable))
            if voiced_count >= minimum_frames:
                median_hz = float(np.median(f0[usable]))
                if median_hz <= male_maximum:
                    role = "male"
                elif median_hz >= female_minimum:
                    role = "female"
        roles.append(role)
        diagnostics.append(
            {
                "line": int(line.get("index", len(diagnostics) + 1)),
                "medianHz": round(median_hz, 2) if median_hz is not None else None,
                "voicedFrameCount": voiced_count,
                "suggestedLeadRole": role,
            }
        )
    return roles, diagnostics


def _separate_role_analysis_stems(
    context: StageContext, settings: dict[str, Any]
) -> dict[str, Any]:
    """Create analysis-only lead and cleaned backing-vocal stems."""
    root = _project_root(context)
    lead_model = str(
        settings.get(
            "leadBackingModelFilename",
            "mel_band_roformer_karaoke_aufr33_viperx_sdr_10.1956.ckpt",
        )
    )
    cleanup_model = str(
        settings.get("backingCleanupModelFilename", "melband_roformer_inst_v2.ckpt")
    )
    gender_model = str(
        settings.get(
            "oppositeGenderCoLeadModelFilename",
            "model_chorus_bs_roformer_ep_267_sdr_24.1275.ckpt",
        )
    )
    if all(
        path.is_file()
        for path in (
            _role_lead(context),
            _role_accompaniment(context),
            _role_backing_vocals(context),
            _role_gender_male(context),
            _role_gender_female(context),
        )
    ):
        return {
            "leadBackingModel": lead_model,
            "backingCleanupModel": cleanup_model,
            "oppositeGenderCoLeadModel": gender_model,
            "leadStem": str(_role_lead(context).resolve()),
            "backingVocalStem": str(_role_backing_vocals(context).resolve()),
            "maleStem": str(_role_gender_male(context).resolve()),
            "femaleStem": str(_role_gender_female(context).resolve()),
            "reused": True,
        }
    _prepend_process_path(Path(_ffmpeg(root)).parent)
    _prepare_cuda_runtime()
    try:
        import torch
        from audio_separator.separator import Separator
    except ImportError as exc:
        raise RuntimeError(
            "Automatic vocal-role analysis requires audio-separator and PyTorch."
        ) from exc

    model_dir = root / "models" / "audio-separator"
    model_dir.mkdir(parents=True, exist_ok=True)
    sample_rate = int(settings.get("roleAnalysisSampleRate", 48000))
    original_torch_load = torch.load

    def compatible_torch_load(*args: Any, **kwargs: Any) -> Any:
        kwargs.setdefault("weights_only", False)
        return original_torch_load(*args, **kwargs)

    torch.load = compatible_torch_load
    lead_separator: Any | None = None
    cleanup_separator: Any | None = None
    gender_separator: Any | None = None
    try:
        lead_separator = Separator(
            output_dir=str(_shared_work(context)),
            model_file_dir=str(model_dir),
            output_format="FLAC",
            sample_rate=sample_rate,
            use_autocast=bool(settings.get("roleAnalysisUseAutocast", True)),
        )
        lead_separator.load_model(model_filename=lead_model)
        lead_separator.separate(
            str(_source_audio(context)),
            {
                "Vocals": _role_lead(context).stem,
                "Instrumental": _role_accompaniment(context).stem,
            },
        )
        cleanup_separator = Separator(
            output_dir=str(_shared_work(context)),
            model_file_dir=str(model_dir),
            output_format="FLAC",
            sample_rate=sample_rate,
            use_autocast=bool(settings.get("roleAnalysisUseAutocast", True)),
            output_single_stem="Vocals",
        )
        cleanup_separator.load_model(model_filename=cleanup_model)
        cleanup_separator.separate(
            str(_role_accompaniment(context)),
            {"Vocals": _role_backing_vocals(context).stem},
        )
        gender_separator = Separator(
            output_dir=str(_shared_work(context)),
            model_file_dir=str(model_dir),
            output_format="FLAC",
            sample_rate=sample_rate,
            use_autocast=bool(settings.get("roleAnalysisUseAutocast", True)),
        )
        gender_separator.load_model(model_filename=gender_model)
        gender_separator.separate(
            str(_vocals(context)),
            {
                "male": _role_gender_male(context).stem,
                "female": _role_gender_female(context).stem,
            },
        )
    finally:
        torch.load = original_torch_load
        del lead_separator, cleanup_separator, gender_separator
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()

    missing = [
        path
        for path in (
            _role_lead(context),
            _role_accompaniment(context),
            _role_backing_vocals(context),
            _role_gender_male(context),
            _role_gender_female(context),
        )
        if not path.is_file()
    ]
    if missing:
        raise RuntimeError(
            "Role analysis separation did not produce: "
            + ", ".join(path.name for path in missing)
        )
    return {
        "leadBackingModel": lead_model,
        "backingCleanupModel": cleanup_model,
        "oppositeGenderCoLeadModel": gender_model,
        "leadStem": str(_role_lead(context).resolve()),
        "backingVocalStem": str(_role_backing_vocals(context).resolve()),
        "maleStem": str(_role_gender_male(context).resolve()),
        "femaleStem": str(_role_gender_female(context).resolve()),
    }


def role_analysis_settings(pipeline: dict[str, Any]) -> dict[str, Any]:
    """Combine immutable aligner provenance with role-specific thresholds."""
    settings = dict(pipeline.get("lyrics", {}))
    settings.update(pipeline.get("roles", {}))
    return settings


def detect_backing_dominant_tail_endpoint(
    lead_audio: Any,
    backing_audio: Any,
    sample_rate: int,
    settings: dict[str, Any],
) -> tuple[float | None, dict[str, Any]]:
    """Find where a lyric lead stops while a backing/ad-lib tail continues.

    The arrays contain only one candidate word, beginning at time zero.  The
    detector is deliberately fail-closed: a low lead alone is insufficient;
    the backing-vocal stem must remain active and dominate for a sustained run.
    """
    import numpy as np

    lead = np.asarray(lead_audio, dtype=np.float32)
    backing = np.asarray(backing_audio, dtype=np.float32)
    if lead.ndim == 1:
        lead = lead[:, None]
    if backing.ndim == 1:
        backing = backing[:, None]
    sample_count = min(len(lead), len(backing))
    lead = lead[:sample_count]
    backing = backing[:sample_count]
    frame_seconds = float(settings.get("leadTailFrameSeconds", 0.04))
    hop_seconds = float(settings.get("leadTailHopSeconds", 0.02))
    frame_size = max(1, round(frame_seconds * sample_rate))
    hop_size = max(1, round(hop_seconds * sample_rate))
    if sample_count < frame_size:
        return None, {"status": "too-short"}

    def rms(values: Any) -> Any:
        energy = np.mean(values.astype(np.float64) ** 2, axis=1)
        window = np.ones(frame_size, dtype=np.float64) / frame_size
        return np.sqrt(np.convolve(energy, window, mode="valid")[::hop_size] + 1e-12)

    lead_rms = rms(lead)
    backing_rms = rms(backing)
    frame_count = min(len(lead_rms), len(backing_rms))
    lead_rms = lead_rms[:frame_count]
    backing_rms = backing_rms[:frame_count]
    minimum_lead_seconds = float(settings.get("minimumLeadOwnedWordSeconds", 0.22))
    minimum_tail_seconds = float(settings.get("minimumBackingAdlibTailSeconds", 0.24))
    reference_end = min(
        frame_count,
        max(1, round(max(minimum_lead_seconds, sample_count / sample_rate * 0.55) / hop_seconds)),
    )
    lead_reference = float(np.percentile(lead_rms[:reference_end], 90))
    backing_reference = float(np.percentile(backing_rms, 90))
    absolute_floor = 10 ** (
        float(settings.get("minimumBackingAdlibActivityDbfs", -52.0)) / 20.0
    )
    if lead_reference < absolute_floor or backing_reference < absolute_floor:
        return None, {
            "status": "insufficient-active-audio",
            "leadReferenceRms": round(lead_reference, 7),
            "backingReferenceRms": round(backing_reference, 7),
        }
    lead_low = lead_reference * 10 ** (
        float(settings.get("leadTailDropDb", -16.0)) / 20.0
    )
    backing_active = max(
        absolute_floor,
        backing_reference
        * 10 ** (float(settings.get("backingTailActivityDropDb", -14.0)) / 20.0),
    )
    dominance_ratio = 10 ** (
        float(settings.get("minimumBackingTailDominanceDb", 6.0)) / 20.0
    )
    eligible = (
        (lead_rms <= lead_low)
        & (backing_rms >= backing_active)
        & (backing_rms >= lead_rms * dominance_ratio)
    )
    first_frame = max(1, math.ceil(minimum_lead_seconds / hop_seconds))
    required_frames = max(1, math.ceil(minimum_tail_seconds / hop_seconds))
    candidate_frame: int | None = None
    for index in range(first_frame, frame_count - required_frames + 1):
        if bool(np.all(eligible[index : index + required_frames])):
            candidate_frame = index
            break
    if candidate_frame is None:
        return None, {
            "status": "no-sustained-backing-dominant-tail",
            "leadReferenceRms": round(lead_reference, 7),
            "backingReferenceRms": round(backing_reference, 7),
        }
    endpoint = candidate_frame * hop_size / sample_rate
    return endpoint, {
        "status": "detected",
        "endpointSeconds": round(endpoint, 4),
        "leadReferenceRms": round(lead_reference, 7),
        "backingReferenceRms": round(backing_reference, 7),
        "leadLowThresholdRms": round(lead_low, 7),
        "backingActiveThresholdRms": round(backing_active, 7),
        "minimumBackingDominanceDb": round(
            20.0 * math.log10(dominance_ratio), 3
        ),
        "sustainedTailSeconds": round(required_frames * hop_seconds, 3),
    }


def trim_backing_adlib_tails_from_lyric_ends(
    lead_path: Path,
    backing_path: Path,
    lines: list[dict[str, Any]],
    settings: dict[str, Any],
) -> dict[str, Any]:
    """End karaoke sweeps at the lyric lead, not at a backing-vocal ad-lib."""
    import soundfile as sf

    report: dict[str, Any] = {
        "status": "passed",
        "policy": "lead-owned-word-end-with-backing-adlib-rejection",
        "candidateCount": 0,
        "trimmedCount": 0,
        "trimmed": [],
    }
    if not bool(settings.get("trimBackingAdlibLyricTails", True)):
        report["status"] = "disabled"
        return report
    final_line_by_group: dict[int, int] = {}
    for index, line in enumerate(lines):
        final_line_by_group[int(line.get("referenceGroup", index + 1))] = index
    minimum_word_seconds = float(settings.get("minimumAdlibTailCandidateSeconds", 0.6))
    minimum_trim_seconds = float(settings.get("minimumAdlibTailTrimSeconds", 0.12))
    with sf.SoundFile(lead_path) as lead_file, sf.SoundFile(backing_path) as backing_file:
        if lead_file.samplerate != backing_file.samplerate:
            raise ValueError("Lead/backing tail analysis sample rates must match")
        sample_rate = int(lead_file.samplerate)
        for group, line_index in final_line_by_group.items():
            line = lines[line_index]
            syllables = line.get("syllables", [])
            if not syllables or str(line.get("role")) == "duet":
                continue
            word = syllables[-1]
            if str(word.get("role", line.get("role"))) == "duet":
                continue
            start = float(word["start"])
            end = float(word["end"])
            if end - start < minimum_word_seconds:
                continue
            report["candidateCount"] += 1
            sample_start = max(0, round(start * sample_rate))
            sample_end = min(
                int(lead_file.frames), int(backing_file.frames), round(end * sample_rate)
            )
            if sample_end <= sample_start:
                continue
            lead_file.seek(sample_start)
            backing_file.seek(sample_start)
            lead_clip = lead_file.read(sample_end - sample_start, dtype="float32", always_2d=True)
            backing_clip = backing_file.read(
                sample_end - sample_start, dtype="float32", always_2d=True
            )
            relative_endpoint, diagnostics = detect_backing_dominant_tail_endpoint(
                lead_clip, backing_clip, sample_rate, settings
            )
            if relative_endpoint is None:
                continue
            endpoint = start + relative_endpoint
            if end - endpoint < minimum_trim_seconds:
                continue
            original_end = end
            word["untrimmedEnd"] = round(original_end, 3)
            word["end"] = round(max(start + 0.01, endpoint), 3)
            word["endSource"] = "lead-end-before-backing-adlib-tail"
            line["end"] = word["end"]
            item = {
                "referenceGroup": group,
                "line": line_index + 1,
                "text": str(word.get("text", "")),
                "role": str(line.get("role", "")),
                "originalEnd": round(original_end, 3),
                "leadOwnedEnd": word["end"],
                "removedTailSeconds": round(original_end - float(word["end"]), 3),
                "diagnostics": diagnostics,
            }
            report["trimmed"].append(item)
    report["trimmedCount"] = len(report["trimmed"])
    return report


def _classify_roles(context: StageContext) -> list[dict[str, Any]]:
    root = _project_root(context)
    pipeline = load_project_config(root)["pipeline"]
    config = role_analysis_settings(pipeline)
    directives = load_source_directives(_source_media(context))
    lyrics = load_json(
        _aligned_lyrics(context)
        if _aligned_lyrics(context).is_file()
        else _lyrics(context)
    )
    role_directives = directives.get("roles", {})
    authoritative = bool(role_directives.get("authoritative", False))
    automatic_roles: list[str | None] | None = None
    pitch_diagnostics: list[dict[str, Any]] = []
    automatic_report: dict[str, Any] | None = None
    if bool(config.get("speakerDiarization", False)) and not authoritative:
        context.progress(5, "Separating lead and backing vocals for role analysis")
        stem_report = _separate_role_analysis_stems(context, config)
        context.progress(35, "Inferring speaker identity and same-lyric co-leads")
        automatic_roles, automatic_report = infer_automatic_vocal_roles(
            root,
            _role_lead(context),
            _role_backing_vocals(context),
            _role_gender_male(context),
            _role_gender_female(context),
            lyrics["lines"],
            config,
            on_progress=lambda percent, message: context.progress(
                35.0 + 0.55 * percent,
                message,
            ),
        )
        context.progress(90, "Applying validated word-level vocal roles")
        automatic_report["stems"] = stem_report
        atomic_write_json(_role_analysis_file(context), automatic_report)
    elif bool(config.get("pitchAssistance", True)) and not authoritative:
        context.progress(10, "Estimating the dominant lyric lead from vocal pitch")
        automatic_roles, pitch_diagnostics = _pitch_assisted_lead_roles(
            _vocals(context), lyrics["lines"], config
        )
    lyrics["lines"], role_report = assign_lead_roles(
        lyrics["lines"], directives, config, automatic_roles=automatic_roles
    )
    if automatic_report is not None:
        lyrics["lines"], word_boundary_report = split_lines_on_syllable_roles(
            lyrics["lines"],
            automatic_report["lineEvidence"],
        )
        role_report["counts"] = word_boundary_report["counts"]
        role_report["wordLevelBoundaries"] = word_boundary_report
        lead_tail_report = trim_backing_adlib_tails_from_lyric_ends(
            _role_lead(context),
            _role_backing_vocals(context),
            lyrics["lines"],
            config,
        )
        role_report["leadOwnedTailTiming"] = lead_tail_report
        automatic_report["leadOwnedTailTiming"] = lead_tail_report
        atomic_write_json(_role_analysis_file(context), automatic_report)
    role_report["pitchDiagnostics"] = pitch_diagnostics
    role_report["automaticInference"] = automatic_report
    lyrics["roles"] = role_report
    atomic_write_json(_lyrics(context), lyrics)
    context.progress(100, f"Assigned lead roles: {role_report['counts']}")
    artifacts = [_artifact(_lyrics(context), "lyrics", "Role-classified karaoke lyrics")]
    if _role_analysis_file(context).is_file():
        artifacts.append(
            _artifact(
                _role_analysis_file(context),
                "analysis",
                "Speaker and co-lead role analysis",
            )
        )
    return artifacts


def _prepare_visuals(context: StageContext) -> list[dict[str, Any]]:
    probe = load_json(_probe_file(context))["lyricRail"]
    if bool(probe.get("hasVideo")):
        context.progress(100, "Source already contains video; keeping its original picture")
        return []

    root = _project_root(context)
    output = _landscape_video(context)
    output.parent.mkdir(parents=True, exist_ok=True)
    context.progress(10, "Creating deterministic local background")
    _run(
        context,
        [
            _ffmpeg(root),
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=0x090d16:s=1920x1080:r=2",
            "-t",
            f"{float(probe['outputDurationSeconds']):.6f}",
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-tune",
            "stillimage",
            "-g",
            "4",
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            str(output),
        ],
        progress=100,
    )
    return [_artifact(output, "video-local-background", "Deterministic local background")]


def _ass_color(hex_color: str, alpha: str = "00") -> str:
    value = hex_color.lstrip("#")
    if len(value) != 6:
        raise ValueError(f"Invalid color: {hex_color}")
    red, green, blue = value[0:2], value[2:4], value[4:6]
    return f"&H{alpha}{blue}{green}{red}"


def _ass_time(seconds: float) -> str:
    seconds = max(0.0, seconds)
    centiseconds = int(round(seconds * 100))
    hours, remainder = divmod(centiseconds, 360000)
    minutes, remainder = divmod(remainder, 6000)
    whole_seconds, cs = divmod(remainder, 100)
    return f"{hours}:{minutes:02d}:{whole_seconds:02d}.{cs:02d}"


def _ass_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")


def _karaoke_text(
    line: dict[str, Any],
    event_start: float,
    *,
    count_in_dots: int = 0,
    count_in_font_size: int = 0,
    lyric_font_size: int = 0,
) -> str:
    parts: list[str] = []
    cursor = event_start
    lead = max(0, int(round((float(line["start"]) - cursor) * 100)))
    if lead and count_in_dots:
        base = lead // count_in_dots
        remainder = lead % count_in_dots
        if count_in_font_size:
            parts.append(f"{{\\fs{count_in_font_size}}}")
        for dot_index in range(count_in_dots):
            duration = base + (1 if dot_index < remainder else 0)
            if dot_index:
                parts.append(r"\h")
            parts.append(f"{{\\kf{max(1, duration)}}}●")
        if lyric_font_size:
            parts.append(f"{{\\fs{lyric_font_size}}}")
        parts.append(r"\h")
        cursor = float(line["start"])
    elif lead:
        parts.append(f"{{\\k{lead}}}\ufeff")
        cursor += lead / 100
    for index, syllable in enumerate(line["syllables"]):
        # Acoustic timing remains immutable in lyrics.json.  The render plan
        # may provide a separately bounded visual interval that removes tiny
        # pauses and excessively abrupt sweeps without moving phrase anchors.
        start = float(syllable.get("visualStart", syllable["start"]))
        end = float(syllable.get("visualEnd", syllable["end"]))
        gap = max(0, int(round((start - cursor) * 100)))
        if index:
            if gap:
                parts.append(f"{{\\k{gap}}} ")
            else:
                parts.append(" ")
        elif gap:
            parts.append(f"{{\\k{gap}}}\ufeff")
        duration = max(1, int(round((end - start) * 100)))
        parts.append(f"{{\\kf{duration}}}{_ass_escape(str(syllable['text']))}")
        cursor = end
    return "".join(parts)


def _smooth_visual_syllables(
    syllables: list[dict[str, Any]],
    *,
    target_minimum_seconds: float,
    maximum_boundary_shift_seconds: float,
    maximum_join_gap_seconds: float,
) -> tuple[list[dict[str, Any]], float]:
    """Create bounded visual sweep intervals while preserving acoustic timing.

    A sweep run is only joined across a small inter-word gap.  Its first and
    last acoustic anchors never move.  Interior boundaries are projected to
    the largest feasible minimum duration, capped by the configured target and
    by the maximum permitted shift from the acoustic boundary.
    """

    visual = [dict(item) for item in syllables]
    if len(visual) < 2 or target_minimum_seconds <= 0:
        for item in visual:
            item["visualStart"] = float(item["start"])
            item["visualEnd"] = float(item["end"])
        return visual, 0.0

    runs: list[tuple[int, int]] = []
    run_start = 0
    for index in range(1, len(visual)):
        gap = float(visual[index]["start"]) - float(visual[index - 1]["end"])
        if gap > maximum_join_gap_seconds:
            runs.append((run_start, index))
            run_start = index
    runs.append((run_start, len(visual)))

    maximum_observed_shift = 0.0
    for left, right in runs:
        run = visual[left:right]
        if len(run) == 1:
            run[0]["visualStart"] = float(run[0]["start"])
            run[0]["visualEnd"] = float(run[0]["end"])
            continue

        targets = [float(run[0]["start"])]
        targets.extend(
            (float(previous["end"]) + float(following["start"])) / 2
            for previous, following in zip(run, run[1:])
        )
        targets.append(float(run[-1]["end"]))
        lower = [targets[0]] + [
            max(float(run[index - 1]["end"]), float(run[index]["start"]))
            - maximum_boundary_shift_seconds
            for index in range(1, len(run))
        ] + [targets[-1]]
        upper = [targets[0]] + [
            min(float(run[index - 1]["end"]), float(run[index]["start"]))
            + maximum_boundary_shift_seconds
            for index in range(1, len(run))
        ] + [targets[-1]]

        def feasible(minimum: float) -> bool:
            boundary = lower[0]
            for index in range(1, len(targets)):
                boundary = max(lower[index], boundary + minimum)
                if boundary > upper[index] + 1e-9:
                    return False
            return True

        low = 0.0
        high = target_minimum_seconds
        for _ in range(32):
            midpoint = (low + high) / 2
            if feasible(midpoint):
                low = midpoint
            else:
                high = midpoint
        minimum = low

        # Project the acoustic boundaries onto the feasible set with bounded
        # isotonic regression. Transforming b[i+1]-b[i] >= minimum into a
        # monotonic constraint on b[i]-i*minimum lets PAVA minimize total
        # squared movement instead of always biasing boundaries left or right.
        transformed_targets = [
            value - index * minimum for index, value in enumerate(targets)
        ]
        transformed_lower = [
            value - index * minimum for index, value in enumerate(lower)
        ]
        transformed_upper = [
            value - index * minimum for index, value in enumerate(upper)
        ]
        blocks: list[dict[str, Any]] = []
        for index, value in enumerate(transformed_targets):
            block = {
                "left": index,
                "right": index + 1,
                "weight": 1.0,
                "sum": value,
                "lower": transformed_lower[index],
                "upper": transformed_upper[index],
            }
            block["value"] = min(
                block["upper"], max(block["lower"], block["sum"])
            )
            blocks.append(block)
            while len(blocks) >= 2 and blocks[-2]["value"] > blocks[-1]["value"]:
                right_block = blocks.pop()
                left_block = blocks.pop()
                merged = {
                    "left": left_block["left"],
                    "right": right_block["right"],
                    "weight": left_block["weight"] + right_block["weight"],
                    "sum": left_block["sum"] + right_block["sum"],
                    "lower": max(left_block["lower"], right_block["lower"]),
                    "upper": min(left_block["upper"], right_block["upper"]),
                }
                if merged["lower"] > merged["upper"] + 1e-9:
                    raise ValueError("Visual timing constraints are infeasible.")
                mean = merged["sum"] / merged["weight"]
                merged["value"] = min(
                    merged["upper"], max(merged["lower"], mean)
                )
                blocks.append(merged)
        transformed = [0.0] * len(targets)
        for block in blocks:
            for index in range(block["left"], block["right"]):
                transformed[index] = float(block["value"])
        boundaries = [
            value + index * minimum for index, value in enumerate(transformed)
        ]

        for index, item in enumerate(run):
            item["visualStart"] = round(boundaries[index], 3)
            item["visualEnd"] = round(boundaries[index + 1], 3)
        maximum_observed_shift = max(
            maximum_observed_shift,
            *(
                max(
                    abs(boundaries[index] - float(run[index - 1]["end"])),
                    abs(boundaries[index] - float(run[index]["start"])),
                )
                for index in range(1, len(targets) - 1)
            ),
        )

    return visual, maximum_observed_shift


def build_karaoke_render_plan(
    lyrics: dict[str, Any], template: dict[str, Any]
) -> dict[str, Any]:
    """Build and validate a conflict-free, renderer-independent lyric plan."""

    layout = template.get("layout", {})
    cue = template.get("roleChangeCue", {})
    preferred_lead = max(0.0, float(layout.get("displayLeadSeconds", 1.6)))
    minimum_lead = max(0.0, float(layout.get("minimumDisplayLeadSeconds", 0.45)))
    preferred_post_hold = max(
        0.0, float(layout.get("preferredPostHoldSeconds", 0.20))
    )
    maximum_post_hold = max(
        preferred_post_hold, float(layout.get("maximumPostHoldSeconds", 2.5))
    )
    slot_gap = max(0.0, float(layout.get("slotTransitionGapSeconds", 0.08)))
    target_visual_minimum = max(
        0.0, float(layout.get("minimumVisualSweepSeconds", 0.0))
    )
    maximum_boundary_shift = max(
        0.0, float(layout.get("maximumVisualBoundaryShiftSeconds", 0.08))
    )
    maximum_join_gap = max(
        0.0, float(layout.get("maximumVisualJoinGapSeconds", 0.12))
    )
    semantic_width_layout = str(layout.get("lineBreakPolicy", "")).lower() in {
        "semantic-width",
        "semantic-audio-width",
    }
    maximum_screen_words = (
        None
        if semantic_width_layout
        else int(layout.get("maximumWordsPerScreen", 10))
    )
    cue_enabled = bool(cue.get("enabled", False))
    cue_transition_only = bool(cue.get("transitionOnly", True))
    cue_after_pause_seconds = max(
        0.0, float(cue.get("showAfterPauseSeconds", 0.0))
    )
    cue_dots = max(0, min(6, int(cue.get("dotCount", 4))))
    cue_seconds = max(0.1, float(cue.get("durationSeconds", 2.0)))
    minimum_cue_seconds = max(
        0.1, float(cue.get("minimumDurationSeconds", minimum_lead))
    )
    minimum_intra_phrase_cue_seconds = max(
        0.1,
        float(cue.get("minimumIntraPhraseDurationSeconds", minimum_lead)),
    )
    cue_required_on_transition = bool(
        cue.get("requiredOnEveryTransition", False)
    )
    required_dot_count = int(cue.get("requiredDotCount", cue_dots))
    maximum_resume_interruption = max(
        0.0,
        float(cue.get("maximumIntraPhraseResumeInterruptionSeconds", 1.2)),
    )
    maximum_resume_gap = max(
        0.0,
        float(cue.get("maximumIntraPhraseResumeGapSeconds", 0.5)),
    )

    source_lines = list(lyrics.get("lines", []))
    events: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    maximum_visual_shift = 0.0
    if cue_required_on_transition and not cue_enabled:
        errors.append({"code": "ROLE_CUE_REQUIRED_BUT_DISABLED"})
    if cue_required_on_transition and cue_dots != required_dot_count:
        errors.append(
            {
                "code": "ROLE_CUE_DOT_COUNT_MISMATCH",
                "observed": cue_dots,
                "required": required_dot_count,
            }
        )
    for index, source_line in enumerate(source_lines):
        line = dict(source_line)
        vocal_start = float(line["start"])
        vocal_end = float(line["end"])
        if vocal_end <= vocal_start:
            errors.append({"code": "NON_POSITIVE_VOCAL_INTERVAL", "line": index + 1})
        role = str(line.get("role") or "male").lower()
        previous_role = (
            str(source_lines[index - 1].get("role") or "male").lower()
            if index
            else None
        )
        previous_vocal_gap = (
            max(
                0.0,
                vocal_start - float(source_lines[index - 1]["end"]),
            )
            if index
            else None
        )
        resumes_after_brief_intra_phrase_role = bool(
            index >= 2
            and role != previous_role
            and role
            == str(source_lines[index - 2].get("role") or "male").lower()
            and str(source_lines[index - 1].get("roleEvidence", ""))
            == "word-level-colead-sequence"
            and source_line.get("referenceGroup")
            == source_lines[index - 1].get("referenceGroup")
            == source_lines[index - 2].get("referenceGroup")
            and float(source_lines[index - 1]["end"])
            - float(source_lines[index - 1]["start"])
            <= maximum_resume_interruption
            and previous_vocal_gap is not None
            and previous_vocal_gap <= maximum_resume_gap
        )
        cue_reason = (
            "initial"
            if index == 0
            else "role-change"
            if role != previous_role and not resumes_after_brief_intra_phrase_role
            else "long-pause"
            if (
                cue_after_pause_seconds > 0.0
                and previous_vocal_gap is not None
                and previous_vocal_gap >= cue_after_pause_seconds
            )
            else "every-line"
            if not cue_transition_only
            else None
        )
        show_role_cue = cue_enabled and cue_dots > 0 and cue_reason is not None
        intra_phrase_boundary = (
            str(line.get("roleEvidence", "")) == "word-level-colead-sequence"
        )
        requested_lead = cue_seconds if show_role_cue else preferred_lead
        visual_syllables, observed_shift = _smooth_visual_syllables(
            list(line.get("syllables", [])),
            target_minimum_seconds=target_visual_minimum,
            maximum_boundary_shift_seconds=maximum_boundary_shift,
            maximum_join_gap_seconds=maximum_join_gap,
        )
        maximum_visual_shift = max(maximum_visual_shift, observed_shift)
        line["syllables"] = visual_syllables
        events.append(
            {
                "lineIndex": index + 1,
                "slot": "top" if line.get("slot") == "top" else "bottom",
                "role": role,
                "showRoleCue": show_role_cue,
                "roleCueReason": cue_reason if show_role_cue else None,
                "roleCueExemptReason": (
                    "resume-after-brief-intra-phrase-role"
                    if resumes_after_brief_intra_phrase_role
                    else None
                ),
                "previousVocalGapSeconds": (
                    round(previous_vocal_gap, 3)
                    if previous_vocal_gap is not None
                    else None
                ),
                "intraPhraseRoleBoundary": intra_phrase_boundary,
                "requestedLeadSeconds": requested_lead,
                "vocalStart": vocal_start,
                "vocalEnd": vocal_end,
                "displayStart": max(0.0, vocal_start - requested_lead),
                "displayEnd": vocal_end + min(preferred_post_hold, maximum_post_hold),
                "line": line,
            }
        )

    by_slot = {
        slot: [event for event in events if event["slot"] == slot]
        for slot in ("top", "bottom")
    }
    for slot, slot_events in by_slot.items():
        for previous, current in zip(slot_events, slot_events[1:]):
            if float(current["vocalStart"]) < float(previous["vocalEnd"]) + slot_gap:
                errors.append(
                    {
                        "code": "SAME_SLOT_VOCAL_OVERLAP",
                        "slot": slot,
                        "lines": [previous["lineIndex"], current["lineIndex"]],
                    }
                )
            earliest_current_start = float(previous["vocalEnd"]) + slot_gap
            if float(current["displayStart"]) < earliest_current_start:
                current["displayStart"] = earliest_current_start
            previous["displayEnd"] = min(
                float(previous["displayEnd"]),
                float(current["displayStart"]) - slot_gap,
            )
            if float(previous["displayEnd"]) < float(previous["vocalEnd"]):
                previous["displayEnd"] = float(previous["vocalEnd"])

    for event in events:
        event["displayStart"] = round(float(event["displayStart"]), 3)
        event["displayEnd"] = round(float(event["displayEnd"]), 3)
        event["effectiveLeadSeconds"] = round(
            float(event["vocalStart"]) - float(event["displayStart"]), 3
        )
        event["effectivePostHoldSeconds"] = round(
            float(event["displayEnd"]) - float(event["vocalEnd"]), 3
        )
        required_lead = (
            minimum_intra_phrase_cue_seconds
            if event["intraPhraseRoleBoundary"]
            else minimum_cue_seconds
            if event["showRoleCue"]
            else minimum_lead
        )
        if event["effectiveLeadSeconds"] < required_lead - 1e-6:
            errors.append(
                {
                    "code": (
                        "ROLE_CUE_LEAD_TOO_SHORT"
                        if event["showRoleCue"]
                        else "DISPLAY_LEAD_TOO_SHORT"
                    ),
                    "slot": event["slot"],
                    "line": event["lineIndex"],
                    "observedSeconds": event["effectiveLeadSeconds"],
                    "minimumSeconds": required_lead,
                }
            )

    if cue_required_on_transition:
        missing_cues = [
            event["lineIndex"]
            for index, event in enumerate(events)
            if (
                index == 0
                or event["role"] != events[index - 1]["role"]
            )
            and event["roleCueExemptReason"] is None
            and not event["showRoleCue"]
        ]
        if missing_cues:
            errors.append(
                {
                    "code": "ROLE_TRANSITION_CUE_MISSING",
                    "lines": missing_cues,
                }
            )

    same_slot_overlap_count = 0
    for slot_events in by_slot.values():
        for previous, current in zip(slot_events, slot_events[1:]):
            if float(previous["displayEnd"]) + slot_gap > float(
                current["displayStart"]
            ) + 1e-6:
                same_slot_overlap_count += 1
    if same_slot_overlap_count:
        errors.append(
            {
                "code": "DISPLAY_SCHEDULE_OVERLAP",
                "observed": same_slot_overlap_count,
                "maximum": 0,
            }
        )

    maximum_active_words = 0
    change_points = sorted(
        {float(event["displayStart"]) for event in events}
        | {float(event["displayEnd"]) for event in events}
    )
    for point in change_points:
        active = [
            event
            for event in events
            if float(event["displayStart"]) <= point < float(event["displayEnd"])
        ]
        active_words = sum(len(event["line"].get("syllables", [])) for event in active)
        maximum_active_words = max(maximum_active_words, active_words)
        if len(active) > 2:
            errors.append(
                {
                    "code": "TOO_MANY_ACTIVE_LYRIC_LINES",
                    "atSeconds": round(point, 3),
                    "observed": len(active),
                    "maximum": 2,
                }
            )
            break
    if (
        maximum_screen_words is not None
        and maximum_active_words > maximum_screen_words
    ):
        errors.append(
            {
                "code": "ACTIVE_SCREEN_WORD_LIMIT_EXCEEDED",
                "observed": maximum_active_words,
                "maximum": maximum_screen_words,
            }
        )

    visual_durations = [
        float(word.get("visualEnd", word["end"]))
        - float(word.get("visualStart", word["start"]))
        for event in events
        for word in event["line"].get("syllables", [])
    ]
    below_target_count = sum(
        duration + 1e-6 < target_visual_minimum for duration in visual_durations
    )
    if target_visual_minimum > 0 and below_target_count:
        warnings.append(
            {
                "code": "VISUAL_SWEEP_BELOW_TARGET",
                "observed": below_target_count,
                "targetMinimumSeconds": target_visual_minimum,
                "minimumObservedSeconds": round(min(visual_durations), 3),
            }
        )

    if maximum_visual_shift > 0:
        warnings.append(
            {
                "code": "VISUAL_TIMING_SMOOTHED",
                "maximumBoundaryShiftSeconds": round(maximum_visual_shift, 3),
            }
        )
    return {
        "schemaVersion": 1,
        "status": "failed" if errors else "passed-with-warnings" if warnings else "passed",
        "errors": errors,
        "warnings": warnings,
        "metrics": {
            "eventCount": len(events),
            "sameSlotOverlapCount": same_slot_overlap_count,
            "maximumActiveWords": maximum_active_words,
            "maximumVisualBoundaryShiftSeconds": round(maximum_visual_shift, 3),
            "visualSweepsBelowTargetCount": below_target_count,
            "minimumVisualSweepSeconds": (
                round(min(visual_durations), 3) if visual_durations else None
            ),
        },
        "events": events,
    }


def build_ass_document(
    lyrics: dict[str, Any],
    template: dict[str, Any],
    *,
    font_path: Path | None = None,
    render_plan: dict[str, Any] | None = None,
) -> str:
    del font_path  # Retained for API compatibility; renderer no longer measures glyphs.
    play_x, play_y = template.get("referenceResolution", [1920, 1080])
    font = template.get("font", {})
    layout = template.get("layout", {})
    unsung = template.get("unsung", {})
    colors = template.get("sung", {}).get("colors", {})
    font_name = str(font.get("family", "Arial"))
    font_size = int(font.get("sizeAt1080p", 64))
    scale_x = int(font.get("scaleX", 100))
    scale_y = int(font.get("scaleY", 100))
    spacing = int(font.get("letterSpacing", 0))
    bold = -1 if font.get("bold", True) else 0
    bottom = int(layout.get("bottomMargin", 70))
    gap = int(layout.get("lineGap", 12))
    top_margin = bottom + int(font_size * scale_y / 100) + gap
    safe_area = float(layout.get("safeAreaPercent", 5))
    safe_x = int(round(play_x * safe_area / 100))
    outer = float(unsung.get("outerOutlineWidth", 6))
    inner = float(template.get("sung", {}).get("innerOutlineWidth", 2))
    shadow = float(unsung.get("shadowOffset", 3))
    waiting = _ass_color(str(unsung.get("fill", "#FFFFFF")))
    black = _ass_color(str(unsung.get("outerOutline", "#000000")))
    inner_color = _ass_color(
        str(template.get("sung", {}).get("innerOutline", "#FFFFFF"))
    )
    transparent = _ass_color("#FFFFFF", "FF")

    header = [
        "[Script Info]",
        "ScriptType: v4.00+",
        "WrapStyle: 2",
        "ScaledBorderAndShadow: yes",
        f"PlayResX: {play_x}",
        f"PlayResY: {play_y}",
        "YCbCr Matrix: TV.709",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, "
        "ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, "
        "MarginR, MarginV, Encoding",
    ]
    for role in ("male", "female", "duet"):
        primary = _ass_color(str(colors.get(role, "#153CFF")))
        for slot, margin in (("Top", top_margin), ("Bottom", bottom)):
            header.append(
                f"Style: Back{slot}{role.title()},{font_name},{font_size},{primary},"
                f"{waiting},{black},{black},{bold},0,0,0,{scale_x},{scale_y},{spacing},0,1,{outer + inner},"
                f"{shadow},2,{safe_x},{safe_x},{margin},1"
            )
            header.append(
                f"Style: Border{slot}{role.title()},{font_name},{font_size},{inner_color},"
                f"{transparent},{transparent},{transparent},{bold},0,0,0,{scale_x},{scale_y},{spacing},0,1,0,"
                f"0,2,{safe_x},{safe_x},{margin},1"
            )
            header.append(
                f"Style: Core{slot}{role.title()},{font_name},{font_size},{primary},"
                f"{waiting},{transparent},{transparent},{bold},0,0,0,{scale_x},{scale_y},{spacing},0,1,0,"
                f"0,2,{safe_x},{safe_x},{margin},1"
            )
    header.extend(
        [
            "",
            "[Events]",
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
        ]
    )

    render_plan = render_plan or build_karaoke_render_plan(lyrics, template)
    if render_plan["errors"]:
        raise ValueError(
            "Karaoke render plan failed quality control: "
            + json.dumps(render_plan["errors"], ensure_ascii=False)
        )
    lines = [event["line"] for event in render_plan["events"]]
    events: list[str] = []
    cue = template.get("roleChangeCue", {})
    cue_enabled = bool(cue.get("enabled", False))
    cue_transition_only = bool(cue.get("transitionOnly", True))
    cue_dots = max(0, min(6, int(cue.get("dotCount", 4))))
    cue_seconds = max(0.1, float(cue.get("durationSeconds", 2.0)))
    cue_font_size = int(cue.get("dotFontSizeAt1080p", round(font_size * 0.72)))
    for index, line in enumerate(lines):
        planned = render_plan["events"][index]
        role_key = str(line.get("role") or "male").lower()
        previous_role = (
            str(lines[index - 1].get("role") or "male").lower() if index else None
        )
        show_role_cue = bool(planned["showRoleCue"])
        start = float(planned["displayStart"])
        end = float(planned["displayEnd"])
        role = role_key.title()
        slot = "Top" if line.get("slot") == "top" else "Bottom"
        margin = top_margin if slot == "Top" else bottom
        # Explicit positioning prevents libass collision avoidance from moving
        # the two outline layers apart. \q2 also guarantees identical wrapping.
        if slot == "Top":
            alignment, x = 1, safe_x
        else:
            alignment, x = 3, int(play_x) - safe_x
        y = int(play_y) - margin
        timed_text = _karaoke_text(
            line,
            start,
            count_in_dots=cue_dots if show_role_cue else 0,
            count_in_font_size=cue_font_size,
            lyric_font_size=int(line.get("fontSizeAt1080p", font_size)),
        )
        line_font_size = int(line.get("fontSizeAt1080p", font_size))
        font_override = (
            f"{{\\fs{line_font_size}}}" if line_font_size != font_size else ""
        )
        position = f"{{\\an{alignment}\\pos({x},{y})\\q2}}"
        sweep_text = position + font_override + timed_text
        events.append(
            f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},"
            f"Back{slot}{role},,0,0,0,,{sweep_text}"
        )
        # Build the white inner border from synchronized offset copies of the
        # exact same karaoke run. Every copy therefore uses libass's own \kf
        # edge; no glyph width, kerning, fallback font, or diacritic position
        # is estimated by LyricRail.
        border_samples = 24
        for sample in range(border_samples):
            angle = 2 * 3.141592653589793 * sample / border_samples
            dx = inner * math.cos(angle)
            dy = inner * math.sin(angle)
            border_position = (
                f"{{\\an{alignment}\\pos({x + dx:.3f},{y + dy:.3f})\\q2}}"
            )
            events.append(
                f"Dialogue: 1,{_ass_time(start)},{_ass_time(end)},"
                f"Border{slot}{role},,0,0,0,,{border_position}{font_override}{timed_text}"
            )
        events.append(
            f"Dialogue: 2,{_ass_time(start)},{_ass_time(end)},"
            f"Core{slot}{role},,0,0,0,,{sweep_text}"
        )
    return "\n".join([*header, *events, ""])


def _render_subtitles(context: StageContext) -> list[dict[str, Any]]:
    root = _project_root(context)
    config = load_project_config(root)["pipeline"]
    template_path = root / str(config.get("render", {}).get("template"))
    template = load_json(template_path)
    lyrics = load_json(_lyrics(context))
    output = _ass(context)
    fonts_directory = root / str(
        config.get("render", {}).get("fontsDirectory", "assets/fonts")
    )
    font_path = fonts_directory / str(template.get("font", {}).get("file", ""))
    render_plan = build_karaoke_render_plan(lyrics, template)
    atomic_write_json(_karaoke_render_plan(context), render_plan)
    if render_plan["errors"]:
        raise ValueError(
            "Karaoke render plan failed quality control: "
            + json.dumps(render_plan["errors"], ensure_ascii=False)
        )
    output.write_text(
        build_ass_document(
            lyrics,
            template,
            font_path=font_path,
            render_plan=render_plan,
        ),
        encoding="utf-8-sig",
    )
    context.progress(100, f"Rendered {lyrics['lineCount']} ASS lines")
    return [
        _artifact(
            _karaoke_render_plan(context),
            "karaoke-render-plan",
            "Validated karaoke render plan",
        ),
        _artifact(output, "subtitle-ass", "Karaoke ASS subtitles"),
    ]


def render_review_preview(
    root: Path,
    job: dict[str, Any],
    *,
    start_seconds: float,
    duration_seconds: float,
) -> Path:
    """Render a short, final-render-equivalent typography review with audio."""

    if start_seconds < 0:
        raise ValueError("Preview start must not be negative.")
    if not 1 <= duration_seconds <= 120:
        raise ValueError("Preview duration must be between 1 and 120 seconds.")
    job_directory = Path(job["paths"]["jobDirectory"])
    shared = job_directory / "work" / "shared"
    probe_data = load_json(shared / "media.json")
    probe = probe_data["lyricRail"]
    output_duration = float(probe["outputDurationSeconds"])
    if start_seconds >= output_duration:
        raise ValueError(
            f"Preview start must be earlier than the song duration ({output_duration:.3f}s)."
        )
    duration_seconds = min(duration_seconds, output_duration - start_seconds)
    pipeline = load_project_config(root)["pipeline"]
    template_path = root / str(pipeline.get("render", {}).get("template"))
    template = load_json(template_path)
    lyrics = load_json(shared / "lyrics.json")
    artifacts = job_directory / "artifacts"
    artifacts.mkdir(parents=True, exist_ok=True)
    review_ass = artifacts / "review.ass"
    review_plan_path = artifacts / "review-render-plan.json"
    fonts_directory = root / str(
        pipeline.get("render", {}).get("fontsDirectory", "assets/fonts")
    )
    font_path = fonts_directory / str(template.get("font", {}).get("file", ""))
    review_plan = build_karaoke_render_plan(lyrics, template)
    atomic_write_json(review_plan_path, review_plan)
    if review_plan["errors"]:
        raise ValueError(
            "Karaoke review render plan failed quality control: "
            + json.dumps(review_plan["errors"], ensure_ascii=False)
        )
    review_ass.write_text(
        build_ass_document(
            lyrics,
            template,
            font_path=font_path,
            render_plan=review_plan,
        ),
        encoding="utf-8-sig",
    )
    output = artifacts / (
        f"review-{start_seconds:06.2f}s-{duration_seconds:05.2f}s.mp4"
    )
    source_media = Path(
        job["request"].get("sourceMedia") or job["request"]["sourceVideo"]
    )
    source = source_media if bool(probe.get("hasVideo")) else shared / "landscape.mp4"
    if not source.is_file():
        raise ValueError("The job has no prepared video source for preview.")
    instrumental = shared / "instrumental.flac"
    if not instrumental.is_file():
        raise ValueError("The job has no instrumental.flac for an audio preview.")
    subtitle_clock_offset = start_seconds
    video_filter = (
        f"setpts=PTS-STARTPTS+{subtitle_clock_offset:.6f}/TB,"
        f"ass='{_filter_path(review_ass)}'"
    )
    if fonts_directory.is_dir():
        video_filter += f":fontsdir='{_filter_path(fonts_directory)}'"
    video_filter += ",setpts=PTS-STARTPTS"
    command = [
        _ffmpeg(root),
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-y",
        "-ss",
        f"{(float(probe['trimStartSeconds']) if bool(probe.get('hasVideo')) else 0.0) + start_seconds:.6f}",
        "-t",
        f"{duration_seconds:.6f}",
        "-i",
        str(source),
        "-ss",
        f"{start_seconds:.6f}",
        "-t",
        f"{duration_seconds:.6f}",
        "-i",
        str(instrumental),
        "-filter_complex",
        (
            f"[0:v:0]{video_filter}[v];"
            f"[1:a:0]atrim=duration={duration_seconds:.6f},"
            "asetpts=PTS-STARTPTS[a]"
        ),
        "-map",
        "[v]",
        "-map",
        "[a]",
        "-c:v",
        "libx264",
        "-profile:v",
        "high",
        "-preset",
        "fast",
        "-crf",
        "18",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "256k",
        "-ar",
        "48000",
        "-ac",
        "2",
        "-movflags",
        "+faststart",
        "-t",
        f"{duration_seconds:.6f}",
        str(output),
    ]
    completed = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if completed.returncode:
        raise RuntimeError(
            "Unable to render preview: "
            + "\n".join(completed.stdout.splitlines()[-20:])
        )
    if not output.is_file() or output.stat().st_size < 500_000:
        raise RuntimeError("Preview output is invalid.")
    return output


def _filter_path(path: Path) -> str:
    value = path.resolve().as_posix().replace("'", "\\'")
    return value.replace(":", "\\:")


def _create_thumbnail(context: StageContext) -> list[dict[str, Any]]:
    lyrics = load_json(_lyrics(context))
    exact_text = _input_lyrics_snapshot(context).read_bytes().decode("utf-8")
    text = next((line for line in exact_text.splitlines() if line.strip()), None)
    first = next(
        (line for line in lyrics.get("lines", []) if str(line.get("text", "")).strip()),
        None,
    )
    if first is None or text is None:
        raise ValueError("A thumbnail requires one non-empty authoritative lyric line")
    if any(ord(character) < 32 and character not in {"\t"} for character in text):
        raise ValueError("The first lyric line contains unsupported control characters")
    timestamp = max(0.0, float(first.get("start", 0.0)))
    root = _project_root(context)
    pipeline = load_project_config(root)["pipeline"]
    template = load_json(root / str(pipeline.get("render", {}).get("template")))
    font = str(template.get("font", {}).get("family", "Be Vietnam Pro"))
    ass = context.work_directory / "thumbnail.ass"
    ass.parent.mkdir(parents=True, exist_ok=True)
    ass.write_text(
        "\n".join(
            [
                "[Script Info]",
                "ScriptType: v4.00+",
                "PlayResX: 640",
                "PlayResY: 360",
                "WrapStyle: 0",
                "ScaledBorderAndShadow: yes",
                "",
                "[V4+ Styles]",
                "Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding",
                f"Style: Thumb,{font},34,&H00FFFFFF,&H00FFFFFF,&H00101010,&H80000000,-1,0,0,0,100,100,0,0,1,3,1,2,38,38,30,1",
                "",
                "[Events]",
                "Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text",
                f"Dialogue: 0,0:00:00.00,0:00:10.00,Thumb,,0,0,0,,{_ass_escape(text)}",
                "",
            ]
        ),
        encoding="utf-8-sig",
        newline="\n",
    )
    base = _thumbnail_base(context)
    output = _thumbnail(context)
    base_filters = (
        "scale=640:360:force_original_aspect_ratio=decrease,"
        "pad=640:360:(ow-iw)/2:(oh-ih)/2:color=0x090d16"
    )
    fonts_directory = root / str(
        pipeline.get("render", {}).get("fontsDirectory", "assets/fonts")
    )
    context.progress(10, "Rendering first-line lyric thumbnail")
    _run(
        context,
        [
            _ffmpeg(root),
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-y",
            "-ss",
            f"{timestamp:.6f}",
            "-i",
            str(_player_video(context)),
            "-frames:v",
            "1",
            "-vf",
            base_filters,
            "-c:v",
            "libwebp",
            "-quality",
            "82",
            "-compression_level",
            "6",
            str(base),
        ],
        progress=50,
    )
    overlay_filter = f"ass='{_filter_path(ass)}'"
    if fonts_directory.is_dir():
        overlay_filter += f":fontsdir='{_filter_path(fonts_directory)}'"
    _run(
        context,
        [
            _ffmpeg(root),
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-y",
            "-i",
            str(base),
            "-frames:v",
            "1",
            "-vf",
            overlay_filter,
            "-c:v",
            "libwebp",
            "-quality",
            "82",
            "-compression_level",
            "6",
            str(output),
        ],
        progress=100,
    )
    if any(
        not path.is_file() or path.stat().st_size == 0 or path.stat().st_size > 1024 * 1024
        for path in (base, output)
    ):
        raise RuntimeError("Thumbnail output is empty or exceeds 1 MiB")
    return [
        _artifact(base, "thumbnail-base", "Representative thumbnail frame"),
        _artifact(output, "thumbnail", "First authoritative lyric line thumbnail"),
    ]


def friendly_delivery_filename(metadata: dict[str, Any], source: Path) -> str:
    source_info = metadata.get("source", {})
    title = replace_unpaired_surrogates(
        str(source_info.get("songTitle", ""))
    ).strip()
    artist = replace_unpaired_surrogates(
        str(source_info.get("referenceArtist", ""))
    ).strip()
    if not title:
        title = re.sub(
            r"\s*\[source\]\s*$",
            "",
            replace_unpaired_surrogates(source.stem),
            flags=re.IGNORECASE,
        )
    stem = f"{title} - {artist}" if artist else title
    # Portable subset for Windows/macOS/Linux and conservative path length.
    stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', " ", stem)
    stem = re.sub(r"\s+", " ", stem).strip(" .")[:180].rstrip(" .")
    return f"{stem or 'karaoke'} [Karaoke].mp4"


def friendly_package_filename(
    metadata: dict[str, Any], source: Path, job_id: str = ""
) -> str:
    base = Path(friendly_delivery_filename(metadata, source)).stem
    suffix = f" {job_id[-6:]}" if job_id else ""
    return f"{base}{suffix}.lrail"


def _render_player_media(context: StageContext) -> list[dict[str, Any]]:
    root = _project_root(context)
    config = load_project_config(root)["pipeline"]
    quality = config.get("quality", {}).get("appPlayback", {})
    probe_data = load_json(_probe_file(context))
    probe = probe_data["lyricRail"]
    has_video = bool(probe.get("hasVideo"))
    source = _source_media(context) if has_video else _landscape_video(context)
    if not source.is_file():
        raise ValueError("Playback video source does not exist")

    duration = float(probe["outputDurationSeconds"])
    trim_start = float(probe.get("trimStartSeconds") or 0.0) if has_video else 0.0
    video_stream = next(
        (
            stream
            for stream in probe_data.get("streams", [])
            if stream.get("codec_type") == "video"
        ),
        {},
    )
    copy_video = (
        str(quality.get("videoPolicy", "")).startswith("stream-copy")
        and trim_start <= 0.001
        and str(video_stream.get("codec_name") or "").lower() == "h264"
    )
    original_plan = _original_audio_delivery_plan(probe_data)
    copy_original = original_plan["mode"] == "bitstream-copy"
    video_output = _player_video(context)
    karaoke_output = _player_karaoke_audio(context)
    original_output = _player_original_audio(context, probe_data)
    video_temporary = video_output.with_name(
        f".{video_output.stem}.partial{video_output.suffix}"
    )
    karaoke_temporary = karaoke_output.with_name(
        f".{karaoke_output.stem}.partial{karaoke_output.suffix}"
    )
    original_temporary = original_output.with_name(
        f".{original_output.stem}.partial{original_output.suffix}"
    )
    for temporary in (video_temporary, karaoke_temporary, original_temporary):
        temporary.unlink(missing_ok=True)
    for stale_original in context.artifacts_directory.glob("original-reference.*"):
        if stale_original != original_output and stale_original.is_file():
            stale_original.unlink()
    audio_filter = f"atrim=duration={duration:.6f},asetpts=PTS-STARTPTS"

    command = [
        _ffmpeg(root),
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-y",
        "-i",
        str(source),
        "-i",
        str(_instrumental(context)),
    ]
    if copy_original:
        if has_video:
            original_map = "0:a:0"
        else:
            command.extend(["-i", str(_source_media(context))])
            original_map = "2:a:0"
    else:
        command.extend(["-i", str(_source_audio(context))])
        original_map = "[original]"

    filter_graph = []
    if not copy_video:
        filter_graph.append(
            f"[0:v:0]trim=start={trim_start:.6f}:duration={duration:.6f},"
            "setpts=PTS-STARTPTS[video]"
        )
    filter_graph.append(f"[1:a:0]{audio_filter}[karaoke]")
    if not copy_original:
        filter_graph.append(f"[2:a:0]{audio_filter}[original]")
    command.extend(["-filter_complex", ";".join(filter_graph)])
    command.extend(["-map", "0:v:0" if copy_video else "[video]"])
    if copy_video:
        command.extend(["-c:v", "copy", "-avoid_negative_ts", "make_zero"])
    else:
        command.extend(
            [
                "-c:v",
                str(quality.get("fallbackVideoCodec", "libx264")),
                "-profile:v",
                str(quality.get("fallbackProfile", "high")),
                "-preset",
                str(quality.get("fallbackPreset", "slow")),
                "-crf",
                str(quality.get("fallbackCrf", 18)),
                "-pix_fmt",
                str(quality.get("fallbackPixelFormat", "yuv420p")),
            ]
        )
    command.extend(
        [
            "-an",
            "-map_metadata",
            "-1",
            "-map_chapters",
            "-1",
            "-t",
            f"{duration:.6f}",
            "-movflags",
            "+faststart",
            str(video_temporary),
        ]
    )
    command.extend(
        [
            "-map",
            "[karaoke]",
            "-vn",
            "-c:a",
            str(quality.get("audioCodec", "aac")),
            "-b:a",
            str(quality.get("audioBitrate", "256k")),
            "-ar",
            str(quality.get("audioSampleRate", 48000)),
            "-ac",
            str(quality.get("audioChannels", 2)),
            "-map_metadata",
            "-1",
            "-map_chapters",
            "-1",
            "-metadata:s:a:0",
            "title=Karaoke",
            "-metadata:s:a:0",
            "language=vie",
            "-disposition:a:0",
            "default",
            "-t",
            f"{duration:.6f}",
            "-movflags",
            "+faststart",
            str(karaoke_temporary),
            "-map",
            original_map,
            "-vn",
        ]
    )
    if copy_original:
        command.extend(["-c:a", "copy", "-avoid_negative_ts", "make_zero"])
    else:
        command.extend(
            [
                "-c:a",
                str(quality.get("audioCodec", "aac")),
                "-b:a",
                str(quality.get("audioBitrate", "256k")),
                "-ar",
                str(quality.get("audioSampleRate", 48000)),
                "-ac",
                str(quality.get("audioChannels", 2)),
            ]
        )
    command.extend(
        [
            "-map_metadata",
            "-1",
            "-map_chapters",
            "-1",
            "-metadata:s:a:0",
            "title=Original Reference",
            "-metadata:s:a:0",
            "language=vie",
            "-disposition:a:0",
            "0",
            "-t",
            f"{duration:.6f}",
        ]
    )
    if original_plan["container"] == "m4a":
        command.extend(["-movflags", "+faststart"])
    else:
        command.extend(["-id3v2_version", "3", "-write_xing", "1"])
    command.append(str(original_temporary))

    context.progress(
        5,
        (
            "Preparing synchronized media with source video and original-audio "
            "bitstream copy"
            if copy_video and copy_original
            else "Preparing synchronized video and dual audio with minimum transcoding"
        ),
    )
    _run(context, command, progress=95)
    for temporary, output in (
        (video_temporary, video_output),
        (karaoke_temporary, karaoke_output),
        (original_temporary, original_output),
    ):
        if not temporary.is_file() or temporary.stat().st_size < 1_024:
            raise RuntimeError(
                f"Playback asset is missing or unexpectedly small: {output.name}"
            )
        os.replace(temporary, output)

    delivered_audio: dict[str, dict[str, Any]] = {}
    for track_id, output in (
        ("karaoke", karaoke_output),
        ("original-reference", original_output),
    ):
        result = _run(
            context,
            [
                _ffprobe(root),
                "-v",
                "error",
                "-select_streams",
                "a:0",
                "-show_entries",
                "stream=codec_name,profile,sample_rate,channels,channel_layout,bit_rate:format=format_name,duration,bit_rate",
                "-of",
                "json",
                str(output),
            ],
        )
        asset_probe = json.loads(result.stdout)
        stream = next(iter(asset_probe.get("streams", [])), {})
        delivered_audio[track_id] = {
            "fileName": output.name,
            "sizeBytes": output.stat().st_size,
            "mediaType": (
                "audio/mp4" if output.suffix.lower() == ".m4a" else "audio/mpeg"
            ),
            "codec": str(stream.get("codec_name") or "unknown"),
            "profile": str(stream.get("profile") or ""),
            "sampleRate": int(stream.get("sample_rate") or 0),
            "channels": int(stream.get("channels") or 0),
            "bitRate": int(stream.get("bit_rate") or 0),
            "durationSeconds": float(
                asset_probe.get("format", {}).get("duration") or 0.0
            ),
        }
    expected_original_codec = str(original_plan["sourceCodec"])
    actual_original_codec = delivered_audio["original-reference"]["codec"]
    if copy_original and actual_original_codec != expected_original_codec:
        raise RuntimeError(
            "Original Reference bitstream-copy verification failed: "
            f"expected {expected_original_codec}, got {actual_original_codec}"
        )
    if not copy_original and actual_original_codec != str(
        quality.get("audioCodec", "aac")
    ):
        raise RuntimeError(
            "Original Reference fallback codec verification failed: "
            f"got {actual_original_codec}"
        )

    playback_report = {
        "schemaVersion": 1,
        "video": {
            "fileName": video_output.name,
            "sizeBytes": video_output.stat().st_size,
            "mode": "bitstream-copy" if copy_video else "h264-quality-fallback",
            "transcodeOccurred": not copy_video,
        },
        "audioTracks": {
            "karaoke": {
                **delivered_audio["karaoke"],
                "mode": "aac-delivery-encode",
                "transcodeOccurred": True,
                "reason": "Karaoke is a newly separated and refined audio mix.",
            },
            "original-reference": {
                **delivered_audio["original-reference"],
                **original_plan,
            },
        },
    }
    atomic_write_json(_playback_media_report(context), playback_report)
    context.log(
        "Playback media uses source video stream-copy"
        if copy_video
        else "Playback media uses a single source-preserving video encode"
    )
    context.log(
        "Original Reference uses "
        f"{original_plan['mode']}: {original_plan['reason']}"
    )
    return [
        _artifact(video_output, "playback-video", "Compact silent playback video"),
        _artifact(karaoke_output, "playback-audio", "Karaoke audio track"),
        _artifact(
            original_output,
            "playback-audio",
            "Original Reference audio track",
        ),
        _artifact(
            _playback_media_report(context),
            "playback-media-report",
            "Verified delivery codec and transcoding decisions",
        ),
    ]


def _ensure_request_bound_package(
    context: StageContext,
    native_cli: str,
    request_path: Path,
    output: Path,
) -> None:
    if output.is_symlink():
        raise RuntimeError(
            f"Existing package output is a symlink and was preserved: {output}"
        )
    if output.exists():
        if not output.is_file():
            raise RuntimeError(
                f"Existing package output is not a regular file and was preserved: {output}"
            )
        context.progress(80, "Authenticating the interrupted package output")
    else:
        context.progress(5, "Encrypting and authenticating the LyricRail package")
        _run(
            context,
            [native_cli, "pack", "--request", str(request_path), "--output", str(output)],
            progress=80,
        )
    _run(
        context,
        [native_cli, "verify-request", str(output), "--request", str(request_path)],
        progress=100,
    )


def _package_lrail(context: StageContext) -> list[dict[str, Any]]:
    root = _project_root(context)
    config = load_project_config(root)["pipeline"]
    package_config = config.get("package", {})
    job = _job(context)
    delivery_metadata = load_json(context.job_directory / "metadata.json")
    source_directives = load_source_directives(_source_media(context))
    release_metadata = build_release_metadata(
        job, delivery_metadata, source_directives, config
    )
    playback_report_path = _playback_media_report(context)
    if not playback_report_path.is_file():
        raise RuntimeError("The verified playback media report is missing")
    playback_report = load_json(playback_report_path)
    release_metadata["playback"]["delivery"] = playback_report
    release_path = context.artifacts_directory / "release-metadata.json"
    atomic_write_json(release_path, release_metadata)
    template_path = root / str(config.get("render", {}).get("template"))
    package_request = build_package_request(
        release_metadata,
        playback_video=_player_video(context),
        karaoke_audio=_player_karaoke_audio(context),
        original_audio=_player_original_audio(context),
        authoritative_lyrics=_input_lyrics_snapshot(context),
        lyrics_timing=_lyrics(context),
        render_plan=_karaoke_render_plan(context),
        release_metadata_file=release_path,
        presentation_template=template_path,
        thumbnail=_thumbnail(context),
        thumbnail_base=_thumbnail_base(context),
        minimum_player_version=str(
            package_config.get("minimumPlayerVersion", "0.8.0")
        ),
    )
    request_path = context.artifacts_directory / "package-request.json"
    atomic_write_json(request_path, package_request)

    output = context.job_directory.parent / friendly_package_filename(
        delivery_metadata, _source_media(context), str(job.get("jobId") or "")
    )
    native_cli = _lrail_cli(root)
    _ensure_request_bound_package(context, native_cli, request_path, output)
    return [
        _artifact(
            output,
            "lrail-package",
            "Authenticated LyricRail karaoke package",
        ),
        _artifact(
            release_path,
            "release-metadata",
            "Professional credits, rights notice, and source attribution",
        ),
        _artifact(
            request_path,
            "lrail-package-request",
            "Reproducible native packaging request",
        ),
    ]


def _cleanup_verified_intermediates(context: StageContext) -> list[dict[str, Any]]:
    """Remove only this job's cleartext trees after re-verifying its package.

    This is ordinary filesystem deletion, not a claim of secure erasure. Flash
    translation layers, snapshots, backups, and filesystem journals may retain
    old blocks after the directory entries are removed.
    """
    root = _project_root(context)
    job = _job(context)
    stages = {stage["key"]: stage["status"] for stage in job.get("stages", [])}
    if stages.get("package_lrail") != "succeeded":
        raise RuntimeError("Cleartext cleanup requires a succeeded package_lrail stage")

    package_candidates = [
        Path(str(artifact.get("path")))
        for artifact in job.get("artifacts", [])
        if artifact.get("kind") == "lrail-package" and artifact.get("path")
    ]
    if len(package_candidates) != 1:
        raise RuntimeError(
            "Cleartext cleanup requires exactly one recorded LyricRail package"
        )
    package = package_candidates[0].resolve()
    job_root = context.job_directory.resolve()
    output_root = job_root.parent
    if (
        not package.is_file()
        or package.suffix.lower() != ".lrail"
        or package.parent != output_root
        or package.is_relative_to(job_root)
    ):
        raise RuntimeError("Recorded package is outside the expected output boundary")

    context.progress(10, "Re-verifying the encrypted package before cleartext cleanup")
    _run(context, [_lrail_cli(root), "verify", str(package)], progress=35)

    removed: list[str] = []
    for name in ("work", "inputs", "artifacts"):
        candidate = (job_root / name).resolve()
        if candidate.parent != job_root or candidate.name != name:
            raise RuntimeError(f"Refusing unsafe cleanup target: {candidate}")
        if candidate.exists():
            shutil.rmtree(candidate)
            removed.append(name)

    report = {
        "schemaVersion": 1,
        "package": package.name,
        "verifiedBeforeCleanup": True,
        "removedJobTrees": removed,
        "sourceMediaRemoved": False,
        "sharedCacheRemoved": False,
        "secureErasureClaimed": False,
        "notice": (
            "Filesystem entries were removed only inside this job. Storage snapshots, "
            "journals, backups, and SSD remapping may retain old blocks."
        ),
    }
    report_path = job_root / "logs" / "cleanup-report.json"
    atomic_write_json(report_path, report)
    context.progress(100, "Verified package retained; cleartext job intermediates removed")
    return [
        _artifact(
            report_path,
            "cleanup-report",
            "Scoped cleartext cleanup audit report",
        )
    ]


def build_local_handlers(root: Path) -> dict[str, StageHandler]:
    del root
    return {
        "probe": _probe,
        "extract_audio": _extract_audio,
        "separate_stems": _separate_stems,
        "load_lyrics": _load_lyrics,
        "align_lyrics": _align_authoritative_lyrics,
        "classify_roles": _classify_roles,
        "prepare_visuals": _prepare_visuals,
        "render_subtitles": _render_subtitles,
        "render_player_media": _render_player_media,
        "create_thumbnail": _create_thumbnail,
        "package_lrail": _package_lrail,
        "cleanup_intermediates": _cleanup_verified_intermediates,
    }
