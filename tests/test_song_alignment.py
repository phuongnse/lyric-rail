from __future__ import annotations

import unittest
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

import torch

from lyricrail.song_alignment import (
    _Segment,
    _ALIGNER_CACHE,
    _acoustic_token,
    _character_voice_evidence,
    evaluate_audio_consensus,
    _snapshot_path,
    _sustained_vocal_end,
    _trusted_line_endpoint,
    _trusted_word_consensus,
    force_align_full_song_lines,
    get_vietnamese_song_aligner,
)


class SongAlignmentTests(unittest.TestCase):
    def test_persistent_worker_reuses_the_loaded_aligner(self) -> None:
        _ALIGNER_CACHE.clear()
        fake = object()
        config = {
            "forcedAlignmentModel": "model",
            "forcedAlignmentModelRevision": "a" * 40,
        }
        with patch.dict(os.environ, {"LYRICRAIL_PERSISTENT_WORKER": "1"}), patch(
            "lyricrail.song_alignment.VietnameseSongAligner", return_value=fake
        ) as constructor:
            first = get_vietnamese_song_aligner(Path("."), config)
            second = get_vietnamese_song_aligner(Path("."), config)
        self.assertIs(first, second)
        constructor.assert_called_once()
        _ALIGNER_CACHE.clear()

    def test_snapshot_path_ignores_incomplete_weight_only_snapshot(self) -> None:
        with TemporaryDirectory() as directory:
            cache = Path(directory)
            snapshots = (
                cache
                / "models--microsoft--wavlm-base-plus-sv"
                / "snapshots"
            )
            incomplete = snapshots / "weights-only"
            incomplete.mkdir(parents=True)
            (incomplete / "model.safetensors").write_bytes(b"weights")
            complete = snapshots / "complete"
            complete.mkdir()
            (complete / "config.json").write_text("{}", encoding="utf-8")
            (complete / "pytorch_model.bin").write_bytes(b"weights")
            (complete / "preprocessor_config.json").write_text(
                "{}", encoding="utf-8"
            )

            self.assertEqual(
                _snapshot_path(cache, "microsoft/wavlm-base-plus-sv"), complete
            )

    def test_snapshot_path_requires_the_pinned_revision_when_supplied(self) -> None:
        with TemporaryDirectory() as directory:
            cache = Path(directory)
            snapshots = cache / "models--owner--model" / "snapshots"
            pinned = snapshots / ("a" * 40)
            pinned.mkdir(parents=True)
            (pinned / "config.json").write_text("{}", encoding="utf-8")
            (pinned / "pytorch_model.bin").write_bytes(b"weights")
            newer = snapshots / ("b" * 40)
            newer.mkdir()
            (newer / "config.json").write_text("{}", encoding="utf-8")
            (newer / "pytorch_model.bin").write_bytes(b"weights")
            (newer / "extra.json").write_text("{}", encoding="utf-8")

            self.assertEqual(
                _snapshot_path(cache, "owner/model", "a" * 40), pinned
            )
            self.assertIsNone(_snapshot_path(cache, "owner/model", "c" * 40))

    def test_character_voice_evidence_separates_consonants_from_vowels(self) -> None:
        evidence = _character_voice_evidence(
            [
                _Segment("d", 0, 1, 0.2),
                _Segment("u", 1, 2, 0.9),
                _Segment("y", 2, 3, 0.8),
                _Segment("\u00ea", 3, 4, 0.85),
                _Segment("n", 4, 5, 0.3),
            ]
        )
        self.assertEqual(evidence["consonantConfidences"], [0.2, 0.3])
        self.assertEqual(evidence["vowelConfidences"], [0.9, 0.8, 0.85])

    def test_acoustic_token_preserves_vietnamese_and_removes_punctuation(self) -> None:
        self.assertEqual(_acoustic_token("Làm..."), "làm")
        self.assertEqual(_acoustic_token("NGƯỜI,"), "người")
        self.assertEqual(_acoustic_token("chán-chường"), "chánchường")

    def test_sustained_vocal_end_uses_measured_energy_drop(self) -> None:
        sample_rate = 1000
        waveform = torch.cat(
            (
                torch.ones((1, sample_rate), dtype=torch.float32),
                torch.zeros((1, sample_rate), dtype=torch.float32),
            ),
            dim=1,
        )

        endpoint = _sustained_vocal_end(
            waveform,
            start=0.0,
            search_end=2.0,
            sample_rate=sample_rate,
        )

        self.assertAlmostEqual(endpoint, 1.0, delta=0.04)

    def test_sustained_vocal_end_adapts_to_residual_stem_noise(self) -> None:
        sample_rate = 1000
        waveform = torch.cat(
            (
                torch.ones((1, sample_rate), dtype=torch.float32),
                torch.full((1, sample_rate), 0.2, dtype=torch.float32),
            ),
            dim=1,
        )

        endpoint = _sustained_vocal_end(
            waveform,
            start=0.0,
            search_end=2.0,
            sample_rate=sample_rate,
        )

        self.assertAlmostEqual(endpoint, 1.0, delta=0.04)

    def test_trusted_line_endpoint_requires_bounded_hold_and_confirmed_gap(self) -> None:
        accepted = _trusted_line_endpoint(
            start=10.0,
            timing_end=12.8,
            next_onset=15.5,
            duration=30.0,
            maximum_hold=3.5,
            is_line_final=True,
        )
        self.assertEqual(accepted, 12.8)
        self.assertIsNone(
            _trusted_line_endpoint(
                start=10.0,
                timing_end=12.8,
                next_onset=12.9,
                duration=30.0,
                maximum_hold=3.5,
                is_line_final=True,
            )
        )
        self.assertIsNone(
            _trusted_line_endpoint(
                start=10.0,
                timing_end=12.8,
                next_onset=15.5,
                duration=30.0,
                maximum_hold=3.5,
                is_line_final=False,
            )
        )
        self.assertEqual(
            _trusted_line_endpoint(
                start=25.0,
                timing_end=27.8,
                next_onset=None,
                duration=31.0,
                maximum_hold=3.5,
                is_line_final=True,
            ),
            27.8,
        )
        self.assertIsNone(
            _trusted_line_endpoint(
                start=25.0,
                timing_end=27.8,
                next_onset=None,
                duration=27.9,
                maximum_hold=3.5,
                is_line_final=True,
            )
        )

    def test_trusted_word_consensus_requires_word_and_onset_agreement(self) -> None:
        self.assertTrue(
            _trusted_word_consensus(
                ctc_start=12.2,
                ctc_confidence=0.01,
                timing_start=12.0,
                timing_match="fuzzy",
                maximum_onset_delta=0.5,
                minimum_weak_match_confidence=0.225,
            )
        )
        self.assertTrue(
            _trusted_word_consensus(
                ctc_start=12.2,
                ctc_confidence=0.25,
                timing_start=12.0,
                timing_match="weak",
                maximum_onset_delta=0.5,
                minimum_weak_match_confidence=0.225,
            )
        )
        self.assertFalse(
            _trusted_word_consensus(
                ctc_start=12.2,
                ctc_confidence=0.01,
                timing_start=12.0,
                timing_match="weak",
                maximum_onset_delta=0.5,
                minimum_weak_match_confidence=0.225,
            )
        )

    def test_source_mix_consensus_uses_calibrated_vowel_floor_and_ratio(self) -> None:
        evidence = evaluate_audio_consensus(
            source_confidence=0.34,
            mean_vowel_confidence=0.26,
            onset_delta=0.03,
            previous_confidence=0.7,
            following_confidence=0.8,
            minimum_word_confidence=0.3,
            secondary_confidence_ratio=0.75,
            minimum_vowel_confidence=0.2,
            minimum_vowel_to_word_ratio=0.45,
            onset_tolerance=0.15,
        )

        self.assertTrue(evidence["accepted"])
        self.assertTrue(all(evidence["gates"].values()))

    def test_source_mix_consensus_rejects_vowel_poor_instrumental_match(self) -> None:
        evidence = evaluate_audio_consensus(
            source_confidence=0.7,
            mean_vowel_confidence=0.1,
            onset_delta=0.03,
            previous_confidence=0.7,
            following_confidence=0.8,
            minimum_word_confidence=0.3,
            secondary_confidence_ratio=0.75,
            minimum_vowel_confidence=0.2,
            minimum_vowel_to_word_ratio=0.45,
            onset_tolerance=0.15,
        )

        self.assertFalse(evidence["accepted"])
        self.assertFalse(evidence["gates"]["vowelConfidence"])
        self.assertFalse(evidence["gates"]["vowelToWordRatio"])

    def test_audio_first_alignment_keeps_long_final_sung_word(self) -> None:
        sample_rate = 16000
        waveform = torch.zeros((1, 20 * sample_rate), dtype=torch.float32)
        waveform[:, 5 * sample_rate : 13 * sample_rate] = 1.0
        aligned_words = [
            {"text": "người", "start": 1.0, "acousticEnd": 1.08, "confidence": 0.99},
            {"text": "lạ", "start": 2.0, "acousticEnd": 2.08, "confidence": 0.99},
            {"text": "nghe", "start": 3.0, "acousticEnd": 3.08, "confidence": 0.99},
            {"text": "em", "start": 5.0, "acousticEnd": 5.08, "confidence": 0.99},
        ]
        for word in aligned_words:
            word["consonantConfidences"] = [0.99]
            word["vowelConfidences"] = [0.99]
        fake_aligner = Mock()
        fake_aligner.model_id = "test-model"
        fake_aligner.device = "cpu"
        fake_aligner.load_audio.return_value = waveform
        fake_aligner.align_song.return_value = aligned_words
        lines = [
            {
                "index": 1,
                "slot": "top",
                "start": 0.0,
                "end": 0.01,
                "text": "người lạ nghe em",
                "role": None,
                "syllables": [
                    {"text": token, "start": 0.0, "end": 0.01}
                    for token in "người lạ nghe em".split()
                ],
            }
        ]

        with patch(
            "lyricrail.song_alignment.VietnameseSongAligner",
            return_value=fake_aligner,
        ):
            output, diagnostics = force_align_full_song_lines(
                Path("."),
                Path("vocals.flac"),
                lines,
                {
                    "maximumWordHoldSeconds": 3.5,
                    "maximumLineFinalHoldSeconds": 12.0,
                    "minimumWordAlignmentConfidence": 0.3,
                },
            )

        final_word = output[0]["syllables"][-1]
        self.assertEqual(final_word["text"], "em")
        self.assertAlmostEqual(final_word["end"], 13.0, delta=0.04)
        self.assertEqual(final_word["endSource"], "vocal-energy-endpoint")
        self.assertEqual(diagnostics["timingEvidence"], "isolated-vocal-audio-only")
        self.assertFalse(
            _trusted_word_consensus(
                ctc_start=13.0,
                ctc_confidence=0.9,
                timing_start=12.0,
                timing_match="exact",
                maximum_onset_delta=0.5,
                minimum_weak_match_confidence=0.225,
            )
        )

    def test_line_endpoint_noise_floor_stops_before_the_next_lyric(self) -> None:
        sample_rate = 16000
        waveform = torch.zeros((1, 20 * sample_rate), dtype=torch.float32)
        # The current held word is quieter than the later lyric. If the later
        # vocal enters the endpoint window it raises the adaptive noise floor
        # and can make the current word look silent immediately.
        waveform[:, 1 * sample_rate : 3 * sample_rate] = 0.3
        waveform[:, 6 * sample_rate : 17 * sample_rate] = 1.0
        aligned_words = [
            {"text": "chường", "start": 1.0, "acousticEnd": 1.08, "confidence": 0.99},
            {"text": "sau", "start": 6.0, "acousticEnd": 6.08, "confidence": 0.99},
        ]
        for word in aligned_words:
            word["consonantConfidences"] = [0.99]
            word["vowelConfidences"] = [0.99]
        fake_aligner = Mock()
        fake_aligner.model_id = "test-model"
        fake_aligner.device = "cpu"
        fake_aligner.load_audio.return_value = waveform
        fake_aligner.align_song.return_value = aligned_words
        lines = [
            {
                "index": index,
                "slot": "top" if index == 1 else "bottom",
                "start": 0.0,
                "end": 0.01,
                "text": token,
                "role": None,
                "syllables": [{"text": token, "start": 0.0, "end": 0.01}],
            }
            for index, token in enumerate(("chường", "sau"), start=1)
        ]

        with patch(
            "lyricrail.song_alignment.VietnameseSongAligner",
            return_value=fake_aligner,
        ):
            output, _ = force_align_full_song_lines(
                Path("."),
                Path("vocals.flac"),
                lines,
                {
                    "maximumWordHoldSeconds": 3.5,
                    "maximumLineFinalHoldSeconds": 12.0,
                    "minimumWordAlignmentConfidence": 0.3,
                },
            )

        self.assertAlmostEqual(
            output[0]["syllables"][0]["end"],
            3.0,
            delta=0.04,
        )


if __name__ == "__main__":
    unittest.main()
