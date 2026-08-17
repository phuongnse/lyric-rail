from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from lyricrail import local_pipeline


FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")


class _Store:
    def __init__(self, job: dict) -> None:
        self.job = job

    def load(self, _job_id: str) -> dict:
        return self.job


class _Context:
    def __init__(self, job_directory: Path, source: Path) -> None:
        self.job_directory = job_directory
        self.artifacts_directory = job_directory / "artifacts"
        self.artifacts_directory.mkdir(parents=True)
        self.job_id = "media-delivery-test"
        self.stage_key = "render_player_media"
        self.store = _Store(
            {"jobId": self.job_id, "request": {"sourceMedia": str(source)}}
        )
        self.messages: list[str] = []

    def progress(self, _percent: float, message: str = "") -> None:
        if message:
            self.messages.append(message)

    def log(self, message: str, _level: str = "INFO") -> None:
        self.messages.append(message)

    def checkpoint(self) -> None:
        return None


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def _packet_hashes(path: Path) -> list[str]:
    result = _run(
        [
            str(FFPROBE),
            "-v",
            "error",
            "-select_streams",
            "a:0",
            "-show_packets",
            "-show_entries",
            "packet=data_hash",
            "-show_data_hash",
            "sha256",
            "-of",
            "json",
            str(path),
        ]
    )
    return [
        str(packet["data_hash"])
        for packet in json.loads(result.stdout).get("packets", [])
    ]


@pytest.mark.skipif(not FFMPEG or not FFPROBE, reason="FFmpeg is not installed")
@pytest.mark.parametrize(
    ("encoder", "source_suffix", "expected_codec", "output_suffix", "media_type"),
    (
        ("aac", ".mp4", "aac", ".m4a", "audio/mp4"),
        ("libmp3lame", ".mkv", "mp3", ".mp3", "audio/mpeg"),
    ),
)
def test_full_portable_original_reference_preserves_encoded_packets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    encoder: str,
    source_suffix: str,
    expected_codec: str,
    output_suffix: str,
    media_type: str,
) -> None:
    source = tmp_path / f"source{source_suffix}"
    _run(
        [
            str(FFMPEG),
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=320x180:rate=24",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=48000",
            "-t",
            "2",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            encoder,
            "-b:a",
            "128k",
            "-movflags",
            "+faststart",
            str(source),
        ]
    )
    job_directory = tmp_path / "job"
    shared = job_directory / "work" / "shared"
    shared.mkdir(parents=True)
    _run(
        [
            str(FFMPEG),
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=220:sample_rate=48000",
            "-t",
            "2",
            "-c:a",
            "flac",
            str(shared / "instrumental.flac"),
        ]
    )
    probe = json.loads(
        _run(
            [
                str(FFPROBE),
                "-v",
                "error",
                "-show_format",
                "-show_streams",
                "-of",
                "json",
                str(source),
            ]
        ).stdout
    )
    duration = float(probe["format"]["duration"])
    probe["lyricRail"] = {
        "hasVideo": True,
        "trimStartSeconds": 0.0,
        "trimEndSeconds": duration,
        "outputDurationSeconds": duration,
    }
    (shared / "media.json").write_text(
        json.dumps(probe), encoding="utf-8"
    )
    quality = {
        "videoPolicy": "stream-copy-when-timeline-allows",
        "audioCodec": "aac",
        "audioBitrate": "256k",
        "audioSampleRate": 48000,
        "audioChannels": 2,
    }
    monkeypatch.setattr(
        local_pipeline,
        "load_project_config",
        lambda _root: {"pipeline": {"quality": {"appPlayback": quality}}},
    )
    monkeypatch.setattr(local_pipeline, "_ffmpeg", lambda _root: str(FFMPEG))
    monkeypatch.setattr(local_pipeline, "_ffprobe", lambda _root: str(FFPROBE))

    context = _Context(job_directory, source)
    artifacts = local_pipeline._render_player_media(context)

    original = context.artifacts_directory / f"original-reference{output_suffix}"
    report = json.loads(
        (context.artifacts_directory / "playback-media.json").read_text(
            encoding="utf-8"
        )
    )
    assert original.is_file()
    assert report["audioTracks"]["original-reference"]["mode"] == "bitstream-copy"
    assert report["audioTracks"]["original-reference"]["transcodeOccurred"] is False
    assert report["audioTracks"]["original-reference"]["codec"] == expected_codec
    assert report["audioTracks"]["original-reference"]["mediaType"] == media_type
    assert _packet_hashes(original) == _packet_hashes(source)
    assert any(item["kind"] == "playback-media-report" for item in artifacts)

    if expected_codec == "aac":
        legacy_reencode = tmp_path / "legacy-original-reference.m4a"
        _run(
            [
                str(FFMPEG),
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                str(source),
                "-map",
                "0:a:0",
                "-c:a",
                "aac",
                "-b:a",
                "256k",
                "-ar",
                "48000",
                "-ac",
                "2",
                str(legacy_reencode),
            ]
        )
        assert original.stat().st_size < legacy_reencode.stat().st_size

        trim_start = 0.25
        trim_end = duration - 0.25
        _run(
            [
                str(FFMPEG),
                "-hide_banner",
                "-loglevel",
                "error",
                "-ss",
                str(trim_start),
                "-to",
                str(trim_end),
                "-i",
                str(source),
                "-map",
                "0:a:0",
                "-c:a",
                "pcm_s24le",
                "-ar",
                "48000",
                "-ac",
                "2",
                str(shared / "source.wav"),
            ]
        )
        probe["lyricRail"].update(
            {
                "trimStartSeconds": trim_start,
                "trimEndSeconds": trim_end,
                "outputDurationSeconds": trim_end - trim_start,
            }
        )
        (shared / "media.json").write_text(
            json.dumps(probe), encoding="utf-8"
        )

        local_pipeline._render_player_media(context)

        fallback_report = json.loads(
            (context.artifacts_directory / "playback-media.json").read_text(
                encoding="utf-8"
            )
        )
        fallback = fallback_report["audioTracks"]["original-reference"]
        assert fallback["mode"] == "aac-fallback"
        assert fallback["transcodeOccurred"] is True
        assert fallback["codec"] == "aac"
        assert fallback["durationSeconds"] == pytest.approx(
            trim_end - trim_start, abs=0.05
        )
