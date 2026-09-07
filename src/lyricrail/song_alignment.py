from __future__ import annotations

import math
import os
import re
import sys
import threading
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


@dataclass(frozen=True)
class _Point:
    token_index: int
    time_index: int
    score: float


@dataclass(frozen=True)
class _Segment:
    label: str
    start: int
    end: int
    score: float


def _trellis(emission: Any, tokens: list[int], blank_id: int) -> Any:
    import torch

    frame_count, token_count = emission.size(0), len(tokens)
    trellis = torch.empty((frame_count + 1, token_count + 1))
    trellis[0, 0] = 0
    trellis[1:, 0] = torch.cumsum(emission[:, blank_id], 0)
    trellis[0, -token_count:] = -float("inf")
    trellis[-token_count:, 0] = float("inf")
    for frame in range(frame_count):
        trellis[frame + 1, 1:] = torch.maximum(
            trellis[frame, 1:] + emission[frame, blank_id],
            trellis[frame, :-1] + emission[frame, tokens],
        )
    return trellis


def _backtrack(
    trellis: Any, emission: Any, tokens: list[int], blank_id: int
) -> list[_Point]:
    import torch

    token_index = trellis.size(1) - 1
    frame_start = torch.argmax(trellis[:, token_index]).item()
    path: list[_Point] = []
    for frame in range(frame_start, 0, -1):
        stayed = trellis[frame - 1, token_index] + emission[frame - 1, blank_id]
        changed = (
            trellis[frame - 1, token_index - 1]
            + emission[frame - 1, tokens[token_index - 1]]
        )
        selected = tokens[token_index - 1] if changed > stayed else blank_id
        path.append(
            _Point(
                token_index - 1,
                frame - 1,
                emission[frame - 1, selected].exp().item(),
            )
        )
        if changed > stayed:
            token_index -= 1
            if token_index == 0:
                break
    if token_index:
        raise RuntimeError("Forced alignment could not consume the lyric text")
    return path[::-1]


def _merge_characters(path: list[_Point], transcript: str) -> list[_Segment]:
    output: list[_Segment] = []
    left = 0
    while left < len(path):
        right = left + 1
        while right < len(path) and path[right].token_index == path[left].token_index:
            right += 1
        score = sum(point.score for point in path[left:right]) / (right - left)
        output.append(
            _Segment(
                transcript[path[left].token_index],
                path[left].time_index,
                path[right - 1].time_index + 1,
                score,
            )
        )
        left = right
    return output


def _merge_words(characters: list[_Segment]) -> list[_Segment]:
    output: list[_Segment] = []
    current: list[_Segment] = []
    sentinel = _Segment("|", 0, 0, 0)
    for character in [*characters, sentinel]:
        if character.label != "|":
            current.append(character)
            continue
        if not current:
            continue
        length = sum(item.end - item.start for item in current)
        score = sum(
            item.score * (item.end - item.start) for item in current
        ) / length
        output.append(
            _Segment(
                "".join(item.label for item in current),
                current[0].start,
                current[-1].end,
                score,
            )
        )
        current = []
    return output


def _split_character_words(characters: list[_Segment]) -> list[list[_Segment]]:
    """Keep the character-level CTC evidence for each aligned word."""
    output: list[list[_Segment]] = []
    current: list[_Segment] = []
    sentinel = _Segment("|", 0, 0, 0)
    for character in [*characters, sentinel]:
        if character.label != "|":
            current.append(character)
            continue
        if current:
            output.append(current)
            current = []
    return output


def _is_vowel_character(character: str) -> bool:
    if not character or not character.isalpha():
        return False
    base = unicodedata.normalize("NFD", character.casefold())[0]
    return base in "aeiouy"


def _character_voice_evidence(characters: list[_Segment]) -> dict[str, Any]:
    """Separate consonant evidence from vowels that a harmony can sustain.

    A word-level CTC score can be high when a backing singer only holds the
    vowels of the displayed lyric. Consonants are the more discriminating
    evidence that the second singer actually articulated the same words.
    """
    consonants = [
        float(item.score)
        for item in characters
        if item.label.isalpha() and not _is_vowel_character(item.label)
    ]
    vowels = [
        float(item.score)
        for item in characters
        if _is_vowel_character(item.label)
    ]
    return {
        "consonantConfidences": consonants,
        "vowelConfidences": vowels,
    }


def _acoustic_token(text: str) -> str:
    return re.sub(r"[^a-zà-ỹđ]+", "", text.casefold())


def _snapshot_path(
    cache: Path, model_id: str, revision: str | None = None
) -> Path | None:
    repository = cache / ("models--" + model_id.replace("/", "--")) / "snapshots"
    if not repository.is_dir():
        return None
    if revision:
        candidate = repository / revision
        if not candidate.is_dir() or not (candidate / "config.json").is_file():
            return None
        if not any(
            (candidate / filename).is_file()
            for filename in ("model.safetensors", "pytorch_model.bin")
        ):
            return None
        return candidate
    complete: list[tuple[int, Path]] = []
    for candidate in repository.iterdir():
        if not candidate.is_dir() or not (candidate / "config.json").is_file():
            continue
        has_weights = any(
            (candidate / filename).is_file()
            for filename in ("model.safetensors", "pytorch_model.bin")
        )
        if not has_weights:
            continue
        complete.append((sum(1 for item in candidate.iterdir() if item.is_file()), candidate))
    return max(complete, default=(0, None), key=lambda item: item[0])[1]


def _sustained_vocal_end(
    waveform: Any,
    *,
    start: float,
    search_end: float,
    sample_rate: int = 16000,
) -> float:
    """Find the first sustained energy drop after a held final syllable."""
    import torch

    sample_start = max(0, round(start * sample_rate))
    sample_end = min(waveform.shape[1], round(search_end * sample_rate))
    clip = waveform[0, sample_start:sample_end]
    frame_size = round(0.04 * sample_rate)
    hop_size = round(0.02 * sample_rate)
    if clip.numel() < frame_size:
        raise ValueError("Not enough audio is available to detect the sustained-word endpoint.")
    frames = clip.unfold(0, frame_size, hop_size)
    rms = torch.sqrt(torch.mean(frames * frames, dim=1) + 1e-12)
    peak = float(torch.max(rms))
    required_frames = max(1, round(0.16 / (hop_size / sample_rate)))
    tail_frames = max(required_frames, round(0.4 / (hop_size / sample_rate)))
    noise_floor = float(torch.median(rms[-tail_frames:]))
    fixed_floor = peak * (10 ** (-18 / 20))
    # Vocal stems can retain a steady instrumental/reverb bed above -18 dB.
    # Adapt to the measured tail floor, while the half-peak cap ensures that a
    # voice sustained through the entire search window is never called silence.
    threshold = min(peak * 0.5, max(fixed_floor, noise_floor * 1.8))
    earliest_frame = max(1, round(0.2 / (hop_size / sample_rate)))
    for index in range(earliest_frame, len(rms) - required_frames + 1):
        if bool(torch.all(rms[index : index + required_frames] < threshold)):
            return start + index * hop_size / sample_rate
    raise ValueError(
        "No reliable vocal endpoint was found for the sustained final word; "
        "the production gate refuses to estimate it."
    )


def _trusted_line_endpoint(
    *,
    start: float,
    timing_end: float,
    next_onset: float | None,
    duration: float,
    maximum_hold: float,
    is_line_final: bool,
) -> float | None:
    """Validate a timing-guide endpoint as evidence for a held line ending.

    This is deliberately constrained: it is only
    useful when the caller has an external timing guide, the word closes a
    lyric line, the hold stays inside the configured production limit, and a
    later vocal onset independently confirms the intervening gap.
    """
    if not is_line_final:
        return None
    hold = timing_end - start
    # A following CTC onset confirms an ordinary inter-line gap. For the last
    # lyric in the media there is no later onset, so the bounded trailing media
    # interval is the corresponding terminal evidence.
    confirmed_gap = (
        next_onset - timing_end
        if next_onset is not None
        else duration - timing_end
    )
    if not (0.2 <= hold <= maximum_hold):
        return None
    if timing_end > duration or confirmed_gap < 0.25:
        return None
    return timing_end


def _trusted_word_consensus(
    *,
    ctc_start: float,
    ctc_confidence: float,
    timing_start: float,
    timing_match: str,
    maximum_onset_delta: float,
    minimum_weak_match_confidence: float,
) -> bool:
    """Accept independent word/onset agreement from a trusted timing guide."""
    if abs(ctc_start - timing_start) > maximum_onset_delta:
        return False
    if timing_match in {"exact", "fuzzy"}:
        return True
    return (
        timing_match == "weak"
        and ctc_confidence >= minimum_weak_match_confidence
    )


def evaluate_audio_consensus(
    *,
    source_confidence: float,
    mean_vowel_confidence: float,
    onset_delta: float,
    previous_confidence: float,
    following_confidence: float,
    minimum_word_confidence: float,
    secondary_confidence_ratio: float,
    minimum_vowel_confidence: float,
    minimum_vowel_to_word_ratio: float,
    onset_tolerance: float,
) -> dict[str, Any]:
    """Score a weak vocal-stem word against an independent source-mix view.

    Absolute vowel scores vary with syllable length and vowel/consonant balance,
    so production acceptance requires both a calibrated floor and a ratio to the
    candidate word score. Timing and both neighboring words remain mandatory.
    """
    vowel_to_word_ratio = mean_vowel_confidence / max(source_confidence, 1e-9)
    gates = {
        "sourceConfidence": (
            source_confidence
            >= minimum_word_confidence * secondary_confidence_ratio
        ),
        "vowelConfidence": mean_vowel_confidence >= minimum_vowel_confidence,
        "vowelToWordRatio": vowel_to_word_ratio >= minimum_vowel_to_word_ratio,
        "onsetAgreement": onset_delta <= onset_tolerance,
        "previousWord": previous_confidence >= minimum_word_confidence,
        "followingWord": following_confidence >= minimum_word_confidence,
    }
    return {
        "accepted": all(gates.values()),
        "gates": gates,
        "vowelToWordConfidenceRatio": round(vowel_to_word_ratio, 4),
    }


_ALIGNER_CACHE: dict[tuple[str, str, str], "VietnameseSongAligner"] = {}
_ALIGNER_CACHE_LOCK = threading.Lock()


def get_vietnamese_song_aligner(
    root: Path, config: dict[str, Any]
) -> "VietnameseSongAligner":
    if os.environ.get("LYRICRAIL_PERSISTENT_WORKER") != "1":
        return VietnameseSongAligner(root, config)
    key = (
        str(root.resolve()),
        str(config.get("forcedAlignmentModel", "nguyenvulebinh/lyric-alignment")),
        str(config.get("forcedAlignmentModelRevision", "")).strip(),
    )
    with _ALIGNER_CACHE_LOCK:
        cached = _ALIGNER_CACHE.get(key)
        if cached is None:
            cached = VietnameseSongAligner(root, config)
            _ALIGNER_CACHE[key] = cached
        return cached


class VietnameseSongAligner:
    """Word-level CTC forced aligner trained specifically for Vietnamese songs."""

    def __init__(self, root: Path, config: dict[str, Any]) -> None:
        try:
            import soundfile
            import torch
            import torchaudio
            from transformers import AutoFeatureExtractor, AutoTokenizer
        except ImportError as exc:
            raise RuntimeError(
                "The forced-alignment runtime is missing; install the LyricRail 'alignment' extra."
            ) from exc

        vendor = root / "vendor" / "lyric-alignment"
        if not (vendor / "model_handling.py").is_file():
            raise RuntimeError(
                "Vietnamese song aligner source is missing from vendor/lyric-alignment."
            )
        sys.path.insert(0, str(vendor))
        from model_handling import Wav2Vec2ForCTC

        self.torch = torch
        self.soundfile = soundfile
        self.torchaudio = torchaudio
        self.model_id = str(
            config.get("forcedAlignmentModel", "nguyenvulebinh/lyric-alignment")
        )
        self.model_revision = str(
            config.get("forcedAlignmentModelRevision", "")
        ).strip()
        if not self.model_revision:
            raise ValueError(
                "forcedAlignmentModelRevision must pin an exact Hugging Face commit"
            )
        cache = root / "models" / "huggingface"
        cache.mkdir(parents=True, exist_ok=True)
        resolved_model: str | Path = (
            _snapshot_path(cache, self.model_id, self.model_revision) or self.model_id
        )
        load_options: dict[str, Any] = {"cache_dir": cache}
        if isinstance(resolved_model, Path):
            load_options["local_files_only"] = True
        else:
            load_options["revision"] = self.model_revision
        self.tokenizer = AutoTokenizer.from_pretrained(resolved_model, **load_options)
        self.feature_extractor = AutoFeatureExtractor.from_pretrained(
            resolved_model, **load_options
        )
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self.model = Wav2Vec2ForCTC.from_pretrained(
            resolved_model, **load_options
        ).eval().to(self.device)
        self.vocabulary = self.tokenizer.get_vocab()
        self.blank_id = self.tokenizer.convert_tokens_to_ids("|")
        self.pad_id = self.tokenizer.convert_tokens_to_ids("<pad>")

    def load_audio(self, path: Path) -> Any:
        samples, sample_rate = self.soundfile.read(
            path, dtype="float32", always_2d=True
        )
        waveform = self.torch.from_numpy(samples.T).mean(dim=0, keepdim=True)
        if sample_rate != 16000:
            waveform = self.torchaudio.functional.resample(
                waveform, sample_rate, 16000
            )
        return waveform

    def _emission(self, clip: Any) -> Any:
        """Run one bounded audio block through the singing CTC model."""
        emission = self._raw_emission(clip)
        emission[:, self.blank_id] = self.torch.maximum(
            emission[:, self.blank_id], emission[:, self.pad_id]
        )
        emission[:, self.pad_id] = -20
        return emission

    def _raw_emission(self, clip: Any) -> Any:
        """Return unmodified CTC log probabilities for forced alignment."""
        values = self.feature_extractor(
            clip.numpy(), sampling_rate=16000, return_tensors="pt"
        ).input_values.to(self.device)
        with self.torch.inference_mode():
            logits = self.model(input_values=values).logits[0]
        emission = self.torch.log_softmax(logits, dim=-1).cpu()
        emission[emission < -20] = -20
        del values, logits
        if self.device == "cuda":
            self.torch.cuda.empty_cache()
        return emission

    def _alignment_tokens(self, raw_words: list[str]) -> tuple[list[str], str, list[int]]:
        acoustic_words = [_acoustic_token(word) for word in raw_words]
        if any(not word for word in acoustic_words):
            raise ValueError(f"Unable to normalize lyrics for alignment: {raw_words}")
        transcript = "|".join(acoustic_words)
        missing = sorted(
            {character for character in transcript if character not in self.vocabulary}
        )
        if missing:
            raise ValueError(f"Characters are outside the forced aligner vocabulary: {missing}")
        return acoustic_words, transcript, [
            self.vocabulary[character] for character in transcript
        ]

    def align_window(
        self,
        waveform: Any,
        *,
        window_start: float,
        window_end: float,
        raw_words: list[str],
    ) -> list[dict[str, Any]]:
        sample_start = max(0, round(window_start * 16000))
        sample_end = min(waveform.shape[1], round(window_end * 16000))
        clip = waveform[0, sample_start:sample_end]
        _, transcript, tokens = self._alignment_tokens(raw_words)
        emission = self._emission(clip)
        trellis = _trellis(emission, tokens, self.blank_id)
        characters = _merge_characters(
            _backtrack(trellis, emission, tokens, self.blank_id), transcript
        )
        aligned = _merge_words(characters)
        character_words = _split_character_words(characters)
        if len(aligned) != len(raw_words) or len(character_words) != len(raw_words):
            raise RuntimeError(
                "Forced aligner returned inconsistent word/character evidence: "
                f"{len(aligned)}/{len(character_words)}/{len(raw_words)}."
            )
        seconds_per_frame = (sample_end - sample_start) / 16000 / emission.shape[0]
        result: list[dict[str, Any]] = []
        for raw, item, word_characters in zip(raw_words, aligned, character_words):
            result.append(
                {
                    "text": raw,
                    "start": window_start + item.start * seconds_per_frame,
                    "acousticEnd": window_start + item.end * seconds_per_frame,
                    "confidence": item.score,
                    **_character_voice_evidence(word_characters),
                }
            )
        del emission, trellis
        if self.device == "cuda":
            self.torch.cuda.empty_cache()
        return result

    def align_song(
        self,
        waveform: Any,
        *,
        raw_words: list[str],
        chunk_seconds: float = 25.0,
        progress: Callable[[int, int], None] | None = None,
    ) -> list[dict[str, Any]]:
        """Force-align exact lyrics to the complete vocal stem.

        Model inference stays bounded by processing independent audio blocks,
        but the CTC trellis is global. This preserves lyric order across
        repeated choruses using only lyric order and acoustic evidence.
        """
        if chunk_seconds < 5.0:
            raise ValueError("Full-song alignment chunks must be at least 5 seconds")
        _, transcript, tokens = self._alignment_tokens(raw_words)
        sample_rate = 16000
        total_samples = int(waveform.shape[1])
        chunk_samples = max(1, round(chunk_seconds * sample_rate))
        chunk_count = max(1, (total_samples + chunk_samples - 1) // chunk_samples)
        emissions: list[Any] = []
        frame_starts: list[float] = []
        frame_ends: list[float] = []
        for chunk_index, sample_start in enumerate(
            range(0, total_samples, chunk_samples), start=1
        ):
            sample_end = min(total_samples, sample_start + chunk_samples)
            emission = self._emission(waveform[0, sample_start:sample_end])
            frame_seconds = (sample_end - sample_start) / sample_rate / emission.shape[0]
            chunk_start_seconds = sample_start / sample_rate
            for frame_index in range(emission.shape[0]):
                frame_starts.append(
                    chunk_start_seconds + frame_index * frame_seconds
                )
                frame_ends.append(
                    chunk_start_seconds + (frame_index + 1) * frame_seconds
                )
            emissions.append(emission)
            if progress:
                progress(chunk_index, chunk_count)

        emission = self.torch.cat(emissions, dim=0)
        trellis = _trellis(emission, tokens, self.blank_id)
        characters = _merge_characters(
            _backtrack(trellis, emission, tokens, self.blank_id), transcript
        )
        aligned = _merge_words(characters)
        character_words = _split_character_words(characters)
        if len(aligned) != len(raw_words) or len(character_words) != len(raw_words):
            raise RuntimeError(
                "Full-song aligner returned inconsistent word/character evidence: "
                f"{len(aligned)}/{len(character_words)}/{len(raw_words)}."
            )
        result: list[dict[str, Any]] = []
        for raw, item, word_characters in zip(raw_words, aligned, character_words):
            result.append(
                {
                    "text": raw,
                    "start": frame_starts[item.start],
                    "acousticEnd": frame_ends[item.end - 1],
                    "confidence": item.score,
                    **_character_voice_evidence(word_characters),
                }
            )
        del emissions, emission, trellis
        if self.device == "cuda":
            self.torch.cuda.empty_cache()
        return result


def force_align_full_song_lines(
    root: Path,
    vocal_path: Path,
    lines: list[dict[str, Any]],
    config: dict[str, Any],
    *,
    progress: Callable[[int, int], None] | None = None,
    final_word_end_seconds: float | None = None,
    source_audio_path: Path | None = None,
    enforce_minimum_confidence: bool = True,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Build karaoke timing from the vocal stem and exact lyric text only."""
    aligner = get_vietnamese_song_aligner(root, config)
    waveform = aligner.load_audio(vocal_path)
    duration = waveform.shape[1] / 16000
    ordinary_maximum_hold = float(config.get("maximumWordHoldSeconds", 3.5))
    line_final_maximum_hold = float(
        config.get("maximumLineFinalHoldSeconds", 12.0)
    )
    minimum_confidence = float(config.get("minimumWordAlignmentConfidence", 0.15))
    chunk_seconds = float(config.get("fullSongAlignmentChunkSeconds", 25.0))
    output = [
        dict(line, syllables=[dict(item) for item in line["syllables"]])
        for line in lines
    ]
    raw_words = [
        str(item["text"]) for line in output for item in line["syllables"]
    ]
    if not raw_words:
        raise ValueError("Exact lyrics are required for audio-first alignment")
    aligned = aligner.align_song(
        waveform,
        raw_words=raw_words,
        chunk_seconds=chunk_seconds,
        progress=progress,
    )

    line_final_word_indices: set[int] = set()
    line_word_ranges: list[tuple[int, int]] = []
    cursor = 0
    for line in output:
        line_start = cursor
        cursor += len(line["syllables"])
        line_word_ranges.append((line_start, cursor))
        line_final_word_indices.add(cursor - 1)

    # A global CTC path may attach a long leading instrumental blank to the
    # first lyric token. Confidence can still look excellent because the token
    # itself was heard somewhere near that blank. Detect these temporally
    # incoherent spans and re-align the phrase around the neighboring sung
    # onsets before any endpoint decisions are made.
    temporal_refinements: list[dict[str, Any]] = []
    for word_index, word in enumerate(aligned):
        acoustic_span = float(word["acousticEnd"]) - float(word["start"])
        if acoustic_span <= line_final_maximum_hold:
            continue
        next_onset = (
            float(aligned[word_index + 1]["start"])
            if word_index + 1 < len(aligned)
            else None
        )
        if next_onset is None:
            temporal_refinements.append(
                {
                    "word": word_index + 1,
                    "text": raw_words[word_index],
                    "oldAcousticSpanSeconds": round(acoustic_span, 3),
                    "accepted": False,
                    "reason": "no-following-onset",
                }
            )
            continue
        line_index = next(
            index
            for index, (left, right) in enumerate(line_word_ranges)
            if left <= word_index < right
        )
        context_word_left = line_word_ranges[line_index][0]
        context_word_right = line_word_ranges[
            min(len(line_word_ranges), line_index + 4) - 1
        ][1]
        window_start = max(0.0, next_onset - ordinary_maximum_hold - 0.5)
        window_end = min(
            duration,
            float(aligned[context_word_right - 1]["start"]) + 3.0,
        )
        candidates: list[dict[str, Any]] = []
        for variant_start, variant_end in {
            (window_start, window_end),
            (float(math.floor(window_start)), float(math.floor(window_end))),
        }:
            if variant_end - variant_start < 2.0:
                continue
            phrase = aligner.align_window(
                waveform,
                window_start=variant_start,
                window_end=variant_end,
                raw_words=raw_words[context_word_left:context_word_right],
            )
            candidate = phrase[word_index - context_word_left]
            candidate_start = float(candidate["start"])
            previous_onset = (
                float(aligned[word_index - 1]["start"])
                if word_index > 0
                else -1.0
            )
            if (
                float(candidate["confidence"]) >= minimum_confidence
                and previous_onset < candidate_start < next_onset
                and next_onset - candidate_start <= ordinary_maximum_hold
                and float(candidate["acousticEnd"]) - candidate_start
                <= ordinary_maximum_hold
            ):
                candidates.append(candidate)
        accepted = bool(candidates)
        if accepted:
            candidate = max(candidates, key=lambda item: float(item["confidence"]))
            aligned[word_index] = candidate
        temporal_refinements.append(
            {
                "word": word_index + 1,
                "text": raw_words[word_index],
                "oldAcousticSpanSeconds": round(acoustic_span, 3),
                "newStart": (
                    round(float(aligned[word_index]["start"]), 3)
                    if accepted
                    else None
                ),
                "newConfidence": (
                    round(float(aligned[word_index]["confidence"]), 4)
                    if accepted
                    else None
                ),
                "accepted": accepted,
            }
        )

    unresolved_temporal_outliers = [
        {
            "word": index + 1,
            "text": raw_words[index],
            "acousticSpanSeconds": round(
                float(word["acousticEnd"]) - float(word["start"]), 3
            ),
        }
        for index, word in enumerate(aligned)
        if float(word["acousticEnd"]) - float(word["start"])
        > line_final_maximum_hold
    ]
    if unresolved_temporal_outliers:
        raise ValueError(
            "Audio-first temporal coherence gate failed: "
            f"{unresolved_temporal_outliers}."
        )

    # Chunked full-song inference is intentionally bounded for long media. A
    # rare low-confidence word can land on weak block evidence even while its
    # global position is correct. Re-align a small, lyric-ordered audio phrase
    # around each such word and accept it only when both confidence and
    # monotonic timing improve. This remains entirely audio-derived.
    local_refinements: list[dict[str, Any]] = []
    weak_indices = [
        index
        for index, word in enumerate(aligned)
        if float(word["confidence"]) < minimum_confidence
    ]
    for weak_index in weak_indices:
        line_index = next(
            index
            for index, (left, right) in enumerate(line_word_ranges)
            if left <= weak_index < right
        )
        context_line_left = max(0, line_index - 2)
        context_line_right = min(len(line_word_ranges), line_index + 3)
        context_word_left = line_word_ranges[context_line_left][0]
        context_word_right = line_word_ranges[context_line_right - 1][1]
        window_start = max(
            0.0, float(aligned[context_word_left]["start"]) - 1.5
        )
        window_end = min(
            duration,
            float(aligned[context_word_right - 1]["start"]) + 4.0,
        )
        refined_phrase = aligner.align_window(
            waveform,
            window_start=window_start,
            window_end=window_end,
            raw_words=raw_words[context_word_left:context_word_right],
        )
        candidate = refined_phrase[weak_index - context_word_left]
        candidate_start = float(candidate["start"])
        previous_start = (
            float(aligned[weak_index - 1]["start"])
            if weak_index > 0
            else -1.0
        )
        following_start = (
            float(aligned[weak_index + 1]["start"])
            if weak_index + 1 < len(aligned)
            else duration + 1.0
        )
        old_confidence = float(aligned[weak_index]["confidence"])
        new_confidence = float(candidate["confidence"])
        accepted = (
            new_confidence > old_confidence
            and previous_start < candidate_start < following_start
        )
        local_refinements.append(
            {
                "word": weak_index + 1,
                "text": raw_words[weak_index],
                "oldConfidence": round(old_confidence, 4),
                "newConfidence": round(new_confidence, 4),
                "accepted": accepted,
                "windowStart": round(window_start, 3),
                "windowEnd": round(window_end, 3),
            }
        )
        if accepted:
            aligned[weak_index] = candidate

    # A separator can attenuate a soft line-initial vowel even when it remains
    # audible in the original mix. For unresolved words, require agreement
    # between two audio views (isolated vocals and source mix), a voiced-vowel
    # trace, stable onset, and strong neighboring words. This replaces the old
    # weak-word consensus path with acoustic evidence only.
    audio_consensus_indices: set[int] = set()
    audio_consensus: list[dict[str, Any]] = []
    unresolved_indices = [
        index
        for index, word in enumerate(aligned)
        if float(word["confidence"]) < minimum_confidence
    ]
    if unresolved_indices and source_audio_path is not None:
        source_waveform = aligner.load_audio(source_audio_path)
        onset_tolerance = float(
            config.get("audioConsensusOnsetToleranceSeconds", 0.15)
        )
        secondary_ratio = float(
            config.get("audioConsensusWeakConfidenceRatio", 0.75)
        )
        minimum_vowel_confidence = float(
            config.get("audioConsensusMinimumVowelConfidence", 0.2)
        )
        minimum_vowel_to_word_ratio = float(
            config.get("audioConsensusMinimumVowelToWordConfidenceRatio", 0.45)
        )
        for weak_index in unresolved_indices:
            line_index = next(
                index
                for index, (left, right) in enumerate(line_word_ranges)
                if left <= weak_index < right
            )
            context_line_left = max(0, line_index - 2)
            context_line_right = min(len(line_word_ranges), line_index + 3)
            context_word_left = line_word_ranges[context_line_left][0]
            context_word_right = line_word_ranges[context_line_right - 1][1]
            attempt_specs = [
                (context_word_left, context_word_right, 1.5, 4.0),
                (
                    max(0, weak_index - 7),
                    min(len(raw_words), weak_index + 9),
                    1.0,
                    2.0,
                ),
                (
                    line_word_ranges[max(0, line_index - 1)][0],
                    line_word_ranges[min(len(line_word_ranges), line_index + 2) - 1][1],
                    1.0,
                    2.5,
                ),
            ]
            candidates: list[tuple[float, dict[str, Any], float, float, float, float]] = []
            seen_attempts: set[tuple[int, int, float, float]] = set()
            for word_left, word_right, left_padding, right_padding in attempt_specs:
                attempt_key = (word_left, word_right, left_padding, right_padding)
                if attempt_key in seen_attempts:
                    continue
                seen_attempts.add(attempt_key)
                window_start = max(
                    0.0, float(aligned[word_left]["start"]) - left_padding
                )
                window_end = min(
                    duration,
                    float(aligned[word_right - 1]["start"]) + right_padding,
                )
                window_variants = {
                    (window_start, window_end),
                    (float(math.floor(window_start)), float(math.floor(window_end))),
                }
                for variant_start, variant_end in window_variants:
                    if variant_end - variant_start < 2.0:
                        continue
                    source_phrase = aligner.align_window(
                        source_waveform,
                        window_start=variant_start,
                        window_end=variant_end,
                        raw_words=raw_words[word_left:word_right],
                    )
                    phrase_index = weak_index - word_left
                    attempt_candidate = source_phrase[phrase_index]
                    previous_confidence = (
                        float(source_phrase[phrase_index - 1]["confidence"])
                        if phrase_index > 0
                        else 1.0
                    )
                    following_confidence = (
                        float(source_phrase[phrase_index + 1]["confidence"])
                        if phrase_index + 1 < len(source_phrase)
                        else 1.0
                    )
                    candidates.append(
                        (
                            float(attempt_candidate["confidence"]),
                            attempt_candidate,
                            previous_confidence,
                            following_confidence,
                            variant_start,
                            variant_end,
                        )
                    )
            (
                _,
                candidate,
                previous_confidence,
                following_confidence,
                selected_window_start,
                selected_window_end,
            ) = max(candidates, key=lambda item: item[0])
            vowel_scores = [
                float(value) for value in candidate.get("vowelConfidences", [])
            ]
            mean_vowel_confidence = (
                sum(vowel_scores) / len(vowel_scores) if vowel_scores else 0.0
            )
            onset_delta = abs(
                float(candidate["start"]) - float(aligned[weak_index]["start"])
            )
            consensus_score = evaluate_audio_consensus(
                source_confidence=float(candidate["confidence"]),
                mean_vowel_confidence=mean_vowel_confidence,
                onset_delta=onset_delta,
                previous_confidence=previous_confidence,
                following_confidence=following_confidence,
                minimum_word_confidence=minimum_confidence,
                secondary_confidence_ratio=secondary_ratio,
                minimum_vowel_confidence=minimum_vowel_confidence,
                minimum_vowel_to_word_ratio=minimum_vowel_to_word_ratio,
                onset_tolerance=onset_tolerance,
            )
            accepted = bool(consensus_score["accepted"])
            audio_consensus.append(
                {
                    "word": weak_index + 1,
                    "text": raw_words[weak_index],
                    "isolatedVocalConfidence": round(
                        float(aligned[weak_index]["confidence"]), 4
                    ),
                    "sourceMixConfidence": round(
                        float(candidate["confidence"]), 4
                    ),
                    "sourceMixMeanVowelConfidence": round(
                        mean_vowel_confidence, 4
                    ),
                    "sourceMixVowelToWordConfidenceRatio": consensus_score[
                        "vowelToWordConfidenceRatio"
                    ],
                    "onsetDeltaSeconds": round(onset_delta, 4),
                    "gates": consensus_score["gates"],
                    "windowStart": round(selected_window_start, 3),
                    "windowEnd": round(selected_window_end, 3),
                    "windowAttemptCount": len(candidates),
                    "accepted": accepted,
                }
            )
            if accepted:
                candidate["confidenceEvidence"] = (
                    "isolated-vocal-and-source-mix-audio-consensus"
                )
                aligned[weak_index] = candidate
                audio_consensus_indices.add(weak_index)

    confidences: list[float] = []
    low_confidence: list[dict[str, Any]] = []
    for word_index, word in enumerate(aligned):
        start = float(word["start"])
        acoustic_end = float(word["acousticEnd"])
        next_onset = (
            float(aligned[word_index + 1]["start"])
            if word_index + 1 < len(aligned)
            else None
        )
        is_line_final = word_index in line_final_word_indices
        maximum_endpoint_search = (
            line_final_maximum_hold if is_line_final else ordinary_maximum_hold
        )
        if (
            next_onset is not None
            and next_onset - 0.02 - start <= ordinary_maximum_hold
        ):
            end = max(acoustic_end, next_onset - 0.02)
            end_source = "next-word-onset"
        elif acoustic_end - start >= 0.35:
            end = acoustic_end
            end_source = "ctc-held-vowel"
        else:
            try:
                end = _sustained_vocal_end(
                    waveform,
                    start=start,
                    search_end=min(
                        duration,
                        start + maximum_endpoint_search + 0.5,
                        # Later lyric activity must not enter the tail used
                        # to estimate this word's vocal noise floor. When a
                        # line-ending hold is followed by a long breath, the
                        # next CTC onset is the strongest audio-only boundary
                        # available for the endpoint search window.
                        (
                            next_onset - 0.02
                            if next_onset is not None
                            else duration
                        ),
                    ),
                )
                end_source = "vocal-energy-endpoint"
            except ValueError as exc:
                is_final_word = word_index == len(aligned) - 1
                if (
                    is_final_word
                    and final_word_end_seconds is not None
                    and start < final_word_end_seconds <= duration
                ):
                    end = float(final_word_end_seconds)
                    end_source = "reviewed-render-endpoint"
                else:
                    raise ValueError(
                        f"Word {word_index + 1} '{word['text']}': {exc}"
                    ) from exc
        word["start"] = round(start, 3)
        word["end"] = round(max(start + 0.01, end), 3)
        word["acousticEnd"] = round(acoustic_end, 3)
        word["confidence"] = round(float(word["confidence"]), 4)
        word["alignmentSource"] = "vietnamese-song-ctc-full-vocal"
        word["endSource"] = end_source
        confidence = float(word["confidence"])
        confidences.append(confidence)
        if confidence < minimum_confidence and word_index not in audio_consensus_indices:
            low_confidence.append(
                {
                    "word": word_index + 1,
                    "text": word["text"],
                    "confidence": word["confidence"],
                }
            )

    cursor = 0
    for line in output:
        count = len(line["syllables"])
        line["syllables"] = aligned[cursor : cursor + count]
        line["start"] = line["syllables"][0]["start"]
        line["end"] = line["syllables"][-1]["end"]
        cursor += count

    diagnostics = {
        "engine": "vietnamese-song-ctc",
        "alignmentMode": "audio-first-full-vocal",
        "timingEvidence": "isolated-vocal-audio-only",
        "model": aligner.model_id,
        "device": aligner.device,
        "chunkSeconds": chunk_seconds,
        "wordCount": len(confidences),
        "minimumConfidenceThreshold": minimum_confidence,
        "minimumConfidence": round(min(confidences), 4),
        "meanConfidence": round(sum(confidences) / len(confidences), 4),
        "lowConfidenceWordCount": len(low_confidence),
        "lowConfidenceWords": low_confidence,
        "temporalRefinementCount": sum(
            int(item["accepted"]) for item in temporal_refinements
        ),
        "temporalRefinements": temporal_refinements,
        "localRefinementCount": sum(
            int(item["accepted"]) for item in local_refinements
        ),
        "localRefinements": local_refinements,
        "audioConsensusAcceptedWordCount": len(audio_consensus_indices),
        "audioConsensusWords": audio_consensus,
        "confidenceGatePassed": not low_confidence,
    }
    if low_confidence and enforce_minimum_confidence:
        details = ", ".join(
            f"word {item['word']} '{item['text']}'={item['confidence']}"
            for item in low_confidence[:8]
        )
        raise ValueError(
            "Audio-first forced alignment confidence gate failed: "
            f"{len(low_confidence)} word(s) below {minimum_confidence}: {details}. "
            f"Audio consensus: {audio_consensus}."
        )
    return output, diagnostics


def force_align_song_lines(
    root: Path,
    vocal_path: Path,
    lines: list[dict[str, Any]],
    config: dict[str, Any],
    *,
    progress: Callable[[int, int], None] | None = None,
    final_word_end_seconds: float | None = None,
    trusted_timing_endpoints: bool = False,
    target_line_indexes: set[int] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    aligner = get_vietnamese_song_aligner(root, config)
    waveform = aligner.load_audio(vocal_path)
    duration = waveform.shape[1] / 16000
    maximum_hold = float(config.get("maximumWordHoldSeconds", 3.5))
    minimum_confidence = float(config.get("minimumWordAlignmentConfidence", 0.15))
    maximum_consensus_onset_delta = float(
        config.get("trustedTimingOnsetToleranceSeconds", 0.5)
    )
    weak_match_confidence_ratio = float(
        config.get("trustedTimingWeakMatchConfidenceRatio", 0.75)
    )
    confidences: list[float] = []
    low_confidence: list[dict[str, Any]] = []
    consensus_accepted: list[dict[str, Any]] = []
    screen_reports: list[dict[str, Any]] = []
    coarse = [
        dict(line, syllables=[dict(item) for item in line["syllables"]])
        for line in lines
    ]
    output = [
        dict(line, syllables=[dict(item) for item in line["syllables"]])
        for line in lines
    ]

    screens = [(index, min(index + 2, len(output))) for index in range(0, len(output), 2)]
    if target_line_indexes is not None:
        screens = [
            (left, right)
            for left, right in screens
            if any(index in target_line_indexes for index in range(left, right))
        ]
    for screen_number, (left, right) in enumerate(screens, start=1):
        target_lines = coarse[left:right]
        target_words = [
            str(item["text"]) for line in target_lines for item in line["syllables"]
        ]
        target_coarse_words = [
            item for line in target_lines for item in line["syllables"]
        ]
        line_final_word_indices: set[int] = set()
        line_word_cursor = 0
        for line in target_lines:
            line_word_cursor += len(line["syllables"])
            line_final_word_indices.add(line_word_cursor - 1)
        # Include up to three neighboring lines on both sides. This retains a
        # complete musical sentence without pulling a repeated chorus from too
        # far away into the same CTC search window.
        # especially to follow the held vowel of a final word instead of
        # collapsing it to a short character emission.
        context_left = left
        for _ in range(3):
            if context_left <= 0:
                break
            gap = float(coarse[context_left]["start"]) - float(
                coarse[context_left - 1]["end"]
            )
            if gap > 6.0:
                break
            context_left -= 1
        context_right = right
        for _ in range(3):
            if context_right >= len(coarse):
                break
            gap = float(coarse[context_right]["start"]) - float(
                coarse[context_right - 1]["end"]
            )
            if gap > 6.0:
                break
            context_right += 1
        context_lines = coarse[context_left:context_right]
        context_words = [
            str(item["text"])
            for line in context_lines
            for item in line["syllables"]
        ]
        target_offset = sum(
            len(line["syllables"]) for line in coarse[context_left:left]
        )
        # Quantize crop boundaries so the same phrase produces identical CTC
        # frames across retries; sub-millisecond crop drift can alter a held
        # final-vowel path in Wav2Vec2.
        window_start = round(
            max(0.0, float(context_lines[0]["start"]) - 1.0), 2
        )
        trailing_gap = (
            float(coarse[context_right]["start"])
            - float(coarse[context_right - 1]["end"])
            if context_right < len(coarse)
            else float("inf")
        )
        tail_context = 3.0 if trailing_gap > 6.0 else 1.5
        raw_window_end = float(context_lines[-1]["end"]) + tail_context
        if trailing_gap > 6.0:
            raw_window_end = round(raw_window_end)
        window_end = min(
            duration,
            raw_window_end,
        )
        aligned = aligner.align_window(
            waveform,
            window_start=window_start,
            window_end=window_end,
            raw_words=context_words,
        )
        target_aligned = aligned[target_offset : target_offset + len(target_words)]
        for word_index, word in enumerate(target_aligned):
            context_word_index = target_offset + word_index
            next_onset = (
                float(aligned[context_word_index + 1]["start"])
                if context_word_index + 1 < len(aligned)
                else None
            )
            start = float(word["start"])
            acoustic_end = float(word["acousticEnd"])
            coarse_word = target_coarse_words[word_index]
            if (
                next_onset is not None
                and next_onset - 0.02 - start <= maximum_hold
            ):
                end = max(acoustic_end, next_onset - 0.02)
                end_source = "next-word-onset"
            elif acoustic_end - start >= 0.35:
                end = acoustic_end
                end_source = "ctc-held-vowel"
            else:
                endpoint_source = "vocal-energy-endpoint"
                try:
                    end = _sustained_vocal_end(
                        waveform,
                        start=start,
                        # Keep 0.5 s after the allowed hold so the detector can
                        # prove that the drop is sustained rather than cutting
                        # exactly at the search boundary.
                        search_end=min(duration, start + maximum_hold + 0.5),
                    )
                except ValueError as exc:
                    trusted_endpoint = (
                        _trusted_line_endpoint(
                            start=start,
                            timing_end=float(coarse_word["end"]),
                            next_onset=next_onset,
                            duration=duration,
                            maximum_hold=maximum_hold,
                            is_line_final=word_index in line_final_word_indices,
                        )
                        if trusted_timing_endpoints
                        else None
                    )
                    is_final_word = (
                        screen_number == len(screens)
                        and word_index == len(target_aligned) - 1
                    )
                    if trusted_endpoint is not None:
                        end = trusted_endpoint
                        endpoint_source = "trusted-timing-guide-endpoint"
                    elif (
                        is_final_word
                        and final_word_end_seconds is not None
                        and start < final_word_end_seconds <= duration
                    ):
                        end = float(final_word_end_seconds)
                        endpoint_source = "reviewed-render-endpoint"
                    else:
                        raise ValueError(
                            f"Screen {screen_number}, word '{word['text']}': {exc}"
                        ) from exc
                end_source = endpoint_source
            word["start"] = round(start, 3)
            word["end"] = round(max(start + 0.01, end), 3)
            word["acousticEnd"] = round(acoustic_end, 3)
            word["confidence"] = round(float(word["confidence"]), 4)
            word["alignmentSource"] = "vietnamese-song-ctc"
            word["endSource"] = end_source
            raw_confidence = float(word["confidence"])
            confidences.append(raw_confidence)
            consensus = (
                trusted_timing_endpoints
                and raw_confidence < minimum_confidence
                and _trusted_word_consensus(
                    ctc_start=start,
                    ctc_confidence=raw_confidence,
                    timing_start=float(coarse_word["start"]),
                    timing_match=str(coarse_word.get("timingGuideMatch", "")),
                    maximum_onset_delta=maximum_consensus_onset_delta,
                    minimum_weak_match_confidence=(
                        minimum_confidence * weak_match_confidence_ratio
                    ),
                )
            )
            if consensus:
                word["confidenceEvidence"] = "ctc-and-trusted-timing-consensus"
                consensus_accepted.append(
                    {
                        "screen": screen_number,
                        "text": word["text"],
                        "ctcConfidence": word["confidence"],
                        "timingGuideMatch": coarse_word.get("timingGuideMatch"),
                        "onsetDeltaSeconds": round(
                            abs(start - float(coarse_word["start"])), 4
                        ),
                    }
                )
            elif raw_confidence < minimum_confidence:
                low_confidence.append(
                    {
                        "screen": screen_number,
                        "text": word["text"],
                        "confidence": word["confidence"],
                        "timingGuideMatch": coarse_word.get("timingGuideMatch"),
                        "onsetDeltaSeconds": round(
                            abs(start - float(coarse_word["start"])), 4
                        ),
                    }
                )

        cursor = 0
        for line_position in range(left, right):
            line = output[line_position]
            count = len(line["syllables"])
            if target_line_indexes is None or line_position in target_line_indexes:
                line["syllables"] = target_aligned[cursor : cursor + count]
                line["start"] = line["syllables"][0]["start"]
                line["end"] = line["syllables"][-1]["end"]
            cursor += count
        screen_reports.append(
            {
                "screen": screen_number,
                "wordCount": len(target_words),
                "windowStart": round(window_start, 3),
                "windowEnd": round(window_end, 3),
                "contextLineStart": context_left + 1,
                "contextLineEnd": context_right,
                "minimumConfidence": round(
                    min(float(item["confidence"]) for item in target_aligned), 4
                ),
            }
        )
        if progress:
            progress(screen_number, len(screens))

    diagnostics = {
        "engine": "vietnamese-song-ctc",
        "model": aligner.model_id,
        "device": aligner.device,
        "wordCount": len(confidences),
        "minimumConfidenceThreshold": minimum_confidence,
        "minimumConfidence": round(min(confidences), 4),
        "meanConfidence": round(sum(confidences) / len(confidences), 4),
        "lowConfidenceWordCount": len(low_confidence),
        "lowConfidenceWords": low_confidence,
        "consensusAcceptedWordCount": len(consensus_accepted),
        "consensusAcceptedWords": consensus_accepted,
        "screens": screen_reports,
        "targetLineIndexes": (
            sorted(target_line_indexes) if target_line_indexes is not None else None
        ),
    }
    if low_confidence:
        details = ", ".join(
            f"screen {item['screen']} '{item['text']}'={item['confidence']} "
            f"guide={item.get('timingGuideMatch')} "
            f"onsetDelta={item.get('onsetDeltaSeconds')}s"
            for item in low_confidence[:8]
        )
        raise ValueError(
            "Forced alignment confidence gate failed: "
            f"{len(low_confidence)} word(s) below {minimum_confidence}: {details}."
        )
    return output, diagnostics
