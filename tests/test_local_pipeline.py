from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np
import soundfile as sf

from lyricrail.local_pipeline import (
    _original_audio_delivery_plan,
    _guarded_lexical_cleanup_candidate,
    _extend_colead_across_speaker_transition,
    _restore_proven_speaker_transition_roles,
    _reconcile_backing_roles_from_word_majority,
    _resolve_short_semantic_group_roles,
    _extend_seeded_colead_semantic_groups,
    _select_residual_consensus_intervals,
    _select_residual_consensus_words,
    assign_lead_roles,
    cluster_speaker_embeddings,
    build_ass_document,
    build_semantic_clause_segments,
    build_karaoke_render_plan,
    decide_colead_roles,
    decide_colead_word_roles,
    detect_backing_dominant_tail_endpoint,
    friendly_delivery_filename,
    friendly_package_filename,
    gate_colead_groups_by_foreground_prominence,
    karaoke_timing_qc,
    lyric_font_size_policy,
    load_vietnamese_punctuation_analyzer,
    promote_opposite_gender_colead_clauses,
    refine_lyric_leakage,
    reflow_aligned_lyric_lines,
    regularize_colead_to_sung_clause_boundaries,
    resolve_ambiguous_semantic_clause_roles,
    resolve_ambiguous_semantic_clause_roles_from_group_context,
    role_analysis_settings,
    select_backing_identity_candidates,
    smooth_roles_with_semantic_group_embeddings,
    stem_separation_qc,
    split_lines_on_syllable_roles,
    _vietnamese_protected_word_boundaries,
)


class LocalPipelineTests(unittest.TestCase):
    def test_backing_adlib_tail_ends_sweep_when_lead_has_stopped(self) -> None:
        sample_rate = 1_000
        time = np.arange(3_000) / sample_rate
        lead = np.zeros_like(time, dtype=np.float32)
        backing = np.zeros_like(time, dtype=np.float32)
        lead[:1_600] = 0.6 * np.sin(2 * np.pi * 110 * time[:1_600])
        backing[1_600:] = 0.5 * np.sin(2 * np.pi * 220 * time[1_600:])
        endpoint, report = detect_backing_dominant_tail_endpoint(
            lead, backing, sample_rate, {}
        )
        self.assertIsNotNone(endpoint)
        self.assertAlmostEqual(float(endpoint), 1.6, delta=0.08)
        self.assertEqual(report["status"], "detected")

    def test_genuine_lead_hold_is_not_cut_by_backing_vocals(self) -> None:
        sample_rate = 1_000
        time = np.arange(3_000) / sample_rate
        lead = 0.6 * np.sin(2 * np.pi * 110 * time)
        backing = 0.35 * np.sin(2 * np.pi * 220 * time)
        endpoint, report = detect_backing_dominant_tail_endpoint(
            lead.astype(np.float32), backing.astype(np.float32), sample_rate, {}
        )
        self.assertIsNone(endpoint)
        self.assertEqual(report["status"], "no-sustained-backing-dominant-tail")

    def test_fixed_karaoke_font_policy_disables_long_line_shrinking(self) -> None:
        self.assertEqual(
            lyric_font_size_policy(
                {"autoShrinkLongLines": False, "minimumFontSize": 104}, 134
            ),
            (False, 134, 134),
        )

    def test_punctuation_analyzer_cache_is_initialized_before_model_loading(self) -> None:
        with TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(
                RuntimeError, "Pinned Vietnamese punctuation model is incomplete"
            ):
                load_vietnamese_punctuation_analyzer(Path(temporary))

    def test_ambiguous_clause_requires_matching_voiceprint_and_pitch(self) -> None:
        roles, report = resolve_ambiguous_semantic_clause_roles(
            [None, None],
            [
                {"cluster": 1, "cosineMargin": 0.08},
                {"cluster": 0, "cosineMargin": 0.08},
            ],
            [196.0, 220.0],
            {"roleByCluster": {0: "female", 1: "male"}},
            {
                "maleMaximumMedianHz": 235.0,
                "femaleMinimumMedianHz": 275.0,
                "minimumSemanticClausePitchResolutionMargin": 0.05,
            },
        )
        self.assertEqual(roles, ["male", None])
        self.assertEqual(report[0]["evidence"], "voiceprint-candidate-confirmed-by-absolute-pitch")

    def test_ambiguous_short_clause_inherits_only_unanimous_group_identity(self) -> None:
        roles, report = resolve_ambiguous_semantic_clause_roles_from_group_context(
            [None, "female", None, "male", "female"],
            [
                {"referenceGroup": 3},
                {"referenceGroup": 3},
                {"referenceGroup": 8},
                {"referenceGroup": 8},
                {"referenceGroup": 8},
            ],
        )
        self.assertEqual(roles, ["female", "female", None, "male", "female"])
        self.assertEqual(
            report[0]["evidence"],
            "unambiguous-sibling-clause-in-authoritative-lyric-group",
        )

    def test_semantic_clauses_ignore_arbitrary_display_line_boundaries(self) -> None:
        lines = [
            {
                "referenceGroup": 17,
                "syllables": [
                    {"text": "Câu", "start": 1.0, "end": 1.2},
                    {"text": "ca", "start": 1.3, "end": 1.5},
                    {"text": "dao,", "start": 1.6, "end": 1.9},
                    {"text": "mẹ", "start": 2.0, "end": 2.2},
                ],
            },
            {
                "referenceGroup": 17,
                "syllables": [
                    {"text": "ru", "start": 2.3, "end": 2.5},
                    {"text": "con", "start": 2.6, "end": 2.9},
                ],
            },
        ]
        clauses, mapping = build_semantic_clause_segments(lines)
        self.assertEqual([item["text"] for item in clauses], ["Câu ca dao,", "mẹ ru con"])
        self.assertEqual(mapping, [[0, 0, 0, 1], [1, 1]])

    def test_vietnamese_lexical_words_protect_internal_display_boundaries(self) -> None:
        words = [
            {"text": text}
            for text in ("Mình", "tôi", "đứng", "rung", "rung", "nghẹn", "ngào")
        ]
        protected, penalties, report = _vietnamese_protected_word_boundaries(words)
        self.assertIn(4, protected)
        self.assertIsInstance(penalties, dict)
        self.assertTrue(any(item["text"] == "rung rung" for item in report))

    def test_curated_vietnamese_idioms_remain_indivisible(self) -> None:
        texts = "kết tóc se duyên mộng chung đôi".split()
        protected, _, report = _vietnamese_protected_word_boundaries(
            [{"text": text} for text in texts],
            constituency_analysis={
                "tokens": [
                    {"text": text, "pos": "NOUN", "head": 0, "deprel": "root"}
                    for text in texts
                ],
                "constituents": [],
            },
        )
        self.assertTrue({1, 3, 5, 6}.issubset(protected))
        self.assertNotIn(2, protected)
        self.assertNotIn(4, protected)

        protected, _, _ = _vietnamese_protected_word_boundaries(
            [{"text": text} for text in "kết tóc se duyên mộng vàng".split()],
            constituency_analysis={
                "tokens": [
                    {"text": "kết tóc", "pos": "VERB"},
                    {"text": "se", "pos": "VERB"},
                    {"text": "duyên mộng", "pos": "ADJ"},
                    {"text": "vàng", "pos": "NOUN"},
                ],
                "constituents": [],
            },
        )
        self.assertNotIn(4, protected)
        self.assertTrue(any(item["text"] == "kết tóc" for item in report))
        self.assertTrue(any(item["text"] == "se duyên" for item in report))

        protected, _, _ = _vietnamese_protected_word_boundaries(
            [{"text": text} for text in "kết tóc se duyên mộng vàng".split()],
            constituency_analysis={
                "tokens": [
                    {"text": "kết", "pos": "VERB", "head": 0, "deprel": "root"},
                    {"text": "tóc", "pos": "NOUN", "head": 1, "deprel": "obj"},
                    {"text": "se", "pos": "VERB", "head": 1, "deprel": "xcomp"},
                    {"text": "duyên", "pos": "NOUN", "head": 3, "deprel": "obj"},
                    {"text": "mộng", "pos": "NOUN", "head": 4, "deprel": "obj"},
                    {"text": "vàng", "pos": "ADJ", "head": 5, "deprel": "amod"},
                ],
                "constituents": [],
            },
        )
        self.assertNotIn(4, protected)

    def test_constituency_penalty_prefers_break_before_temporal_noun_phrase(self) -> None:
        words = [
            {"text": text}
            for text in ("Tôi", "nhớ", "mãi", "năm", "xưa", "một", "chiều")
        ]
        analysis = {
            "tokens": [
                {"text": "Tôi", "pos": "PRON"},
                {"text": "nhớ", "pos": "VERB"},
                {"text": "mãi", "pos": "ADV"},
                {"text": "năm xưa", "pos": "NOUN"},
                {"text": "một chiều", "pos": "NOUN"},
            ],
            "constituents": [
                {"label": "NP", "startToken": 0, "endToken": 1},
                {"label": "NP", "startToken": 3, "endToken": 5},
                {"label": "VP", "startToken": 1, "endToken": 5},
                {"label": "S", "startToken": 0, "endToken": 5},
                {"label": "ROOT", "startToken": 0, "endToken": 5},
            ],
        }
        protected, penalties, _ = _vietnamese_protected_word_boundaries(
            words, constituency_analysis=analysis
        )
        self.assertIn(4, protected)
        self.assertIn(6, protected)
        self.assertGreater(penalties[5], penalties[3])

    def test_vietnamese_syntax_protects_compounds_and_short_complements(self) -> None:
        cases = (
            (
                ("câu", "nói"),
                [
                    {"text": "câu", "pos": "NOUN", "head": 0, "deprel": "root"},
                    {
                        "text": "nói",
                        "pos": "VERB",
                        "head": 1,
                        "deprel": "compound:vmod",
                    },
                ],
            ),
            (
                ("chuốt", "lấy"),
                [
                    {"text": "chuốt", "pos": "VERB", "head": 0, "deprel": "root"},
                    {"text": "lấy", "pos": "VERB", "head": 1, "deprel": "xcomp"},
                ],
            ),
            (
                ("nhiệm", "màu,"),
                [
                    {"text": "nhiệm", "pos": "VERB", "head": 0, "deprel": "root"},
                    {"text": "màu", "pos": "NOUN", "head": 1, "deprel": "obj"},
                ],
            ),
        )
        for texts, tokens in cases:
            protected, _, _ = _vietnamese_protected_word_boundaries(
                [{"text": text} for text in texts],
                constituency_analysis={
                    "tokens": tokens,
                    "constituents": [
                        {"label": "ROOT", "startToken": 0, "endToken": 2}
                    ],
                },
            )
            self.assertIn(1, protected, texts)

        protected, _, _ = _vietnamese_protected_word_boundaries(
            [
                {"text": text}
                for text in ("Sợ", "năm", "tháng", "duyên", "kia", "nhạt", "nhòa")
            ],
            constituency_analysis={
                "tokens": [
                    {"text": "Sợ", "pos": "VERB", "head": 0, "deprel": "root"},
                    {
                        "text": "năm tháng",
                        "pos": "NOUN",
                        "head": 1,
                        "deprel": "obj",
                    },
                    {
                        "text": "duyên",
                        "pos": "VERB",
                        "head": 2,
                        "deprel": "compound:vmod",
                    },
                    {"text": "kia", "pos": "DET", "head": 3, "deprel": "det"},
                    {"text": "nhạt", "pos": "ADJ", "head": 3, "deprel": "amod"},
                    {"text": "nhòa", "pos": "ADJ", "head": 5, "deprel": "fixed"},
                ],
                "constituents": [
                    {"label": "ROOT", "startToken": 0, "endToken": 6}
                ],
            },
        )
        self.assertIn(2, protected)
        self.assertNotIn(3, protected)

    def test_role_analysis_inherits_pinned_alignment_provenance(self) -> None:
        settings = role_analysis_settings(
            {
                "lyrics": {
                    "forcedAlignmentModel": "owner/alignment",
                    "forcedAlignmentModelRevision": "a" * 40,
                    "shared": "lyrics",
                },
                "roles": {
                    "speakerEmbeddingModel": "owner/speaker",
                    "shared": "roles",
                },
            }
        )

        self.assertEqual(settings["forcedAlignmentModel"], "owner/alignment")
        self.assertEqual(settings["forcedAlignmentModelRevision"], "a" * 40)
        self.assertEqual(settings["speakerEmbeddingModel"], "owner/speaker")
        self.assertEqual(settings["shared"], "roles")

    def test_full_semantic_group_embedding_resolves_display_split_outlier(self) -> None:
        roles, report = smooth_roles_with_semantic_group_embeddings(
            ["female", "male", "male"],
            [4, 4, 5],
            {4: "female", 5: "male"},
            {
                4: {"role": "female", "cosineMargin": 0.24},
                5: {"role": "male", "cosineMargin": 0.31},
            },
        )

        self.assertEqual(roles, ["female", "female", "male"])
        self.assertEqual(report[0]["status"], "resolved")
        self.assertEqual(report[0]["changedLineIndexes"], [2])

    def test_weak_semantic_group_embedding_does_not_guess(self) -> None:
        roles, report = smooth_roles_with_semantic_group_embeddings(
            ["female", "male"],
            [4, 4],
            {4: None},
            {4: {"role": None, "cosineMargin": 0.02}},
        )

        self.assertEqual(roles, ["female", "male"])
        self.assertEqual(report[0]["status"], "unresolved")

    def test_guarded_cleanup_is_word_local_and_reduces_coherent_leakage(self) -> None:
        sample_rate = 16_000
        time = np.arange(sample_rate * 2, dtype=np.float32) / sample_rate
        active = ((time >= 0.75) & (time <= 1.25)).astype(np.float32)
        vocal = 0.3 * np.sin(2 * np.pi * 220 * time) * active
        music = (
            0.18 * np.sin(2 * np.pi * 880 * time)
            + 0.12 * np.sin(2 * np.pi * 1_230 * time)
        )
        instrumental = music + 0.08 * vocal
        instrumental_stereo = np.column_stack((instrumental, instrumental))
        vocal_stereo = np.column_stack((vocal, vocal))
        candidate, report = _guarded_lexical_cleanup_candidate(
            instrumental_stereo,
            vocal_stereo,
            sample_rate,
            [(0.75, 1.25)],
            {
                "guardedCleanupFftSize": 1_024,
                "guardedCleanupHopSize": 256,
                "guardedCleanupFadeSeconds": 0.08,
                "guardedCleanupPaddingSeconds": 0.0,
                "guardedCleanupMinimumCoherence": 0.0,
                "guardedCleanupCoherenceSpan": 0.1,
                "guardedCleanupMaximumCoefficient": 1.0,
                "guardedCleanupSmoothingFrames": 5,
                "guardedCleanupMaximumHz": 1_000.0,
            },
            strength=1.0,
        )
        outside = (time < 0.67) | (time > 1.33)
        np.testing.assert_array_equal(
            candidate[outside], instrumental_stereo[outside]
        )
        inside = active.astype(bool)
        vocal_window = vocal[inside].astype("float64")
        denominator = float(np.dot(vocal_window, vocal_window))
        before = abs(
            float(np.dot(instrumental[inside], vocal_window)) / denominator
        )
        after = abs(
            float(np.dot(candidate[inside, 0], vocal_window)) / denominator
        )
        self.assertLess(after, before * 0.5)
        self.assertEqual(report["outsideMaximumAbsoluteDelta"], 0.0)
        self.assertGreater(report["localMusicPreservationSnrDb"], 15.0)
        self.assertLessEqual(report["maximumPostCoherentLeakageDb"], -100.0)

    def test_residual_cleanup_requires_two_model_acoustic_consensus(self) -> None:
        words = [
            {
                "text": "instrument-like-false-positive",
                "start": 10.0,
                "preCoherentLeakageDb": -9.0,
            },
            {
                "text": "real-residual",
                "start": 20.0,
                "preCoherentLeakageDb": -14.0,
            },
        ]
        selected, report = _select_residual_consensus_words(
            words,
            [
                {
                    "wordIndex": 0,
                    "residualConfidence": 0.8,
                    "residualConsonantConfidence": 0.7,
                },
                {
                    "wordIndex": 1,
                    "residualConfidence": 0.4,
                    "residualConsonantConfidence": 0.65,
                },
            ],
            [
                {
                    "wordIndex": 0,
                    "confidence": 0.2,
                    "consonantConfidence": 0.2,
                    "vocalCorrelation": 0.01,
                },
                {
                    "wordIndex": 1,
                    "confidence": 0.42,
                    "consonantConfidence": 0.64,
                    "vocalCorrelation": 0.045,
                },
            ],
            {},
        )
        self.assertEqual(selected, [1])
        self.assertEqual(report["primaryCandidateCount"], 2)
        self.assertEqual(report["acceptedWordCount"], 1)

    def test_unaligned_residual_consensus_rejects_quiet_or_unrelated_stems(self) -> None:
        sample_rate = 8_000
        time = np.arange(sample_rate, dtype=np.float32) / sample_rate
        primary = 0.2 * np.sin(2 * np.pi * 220 * time)
        confirmed = 0.08 * np.sin(2 * np.pi * 220 * time)
        quiet = 0.00002 * np.sin(2 * np.pi * 220 * time)
        unrelated = 0.08 * np.sin(2 * np.pi * 1_100 * time)
        primary_stereo = np.column_stack((primary, primary))
        secondary = np.column_stack(
            (
                np.concatenate((confirmed[:2_000], quiet[2_000:4_000], unrelated[4_000:])),
                np.concatenate((confirmed[:2_000], quiet[2_000:4_000], unrelated[4_000:])),
            )
        )
        intervals = [
            {"start": 0.0, "end": 0.25},
            {"start": 0.25, "end": 0.5},
            {"start": 0.5, "end": 1.0},
        ]

        accepted, report = _select_residual_consensus_intervals(
            primary_stereo,
            secondary,
            sample_rate,
            intervals,
            {
                "minimumResidualConsensusRmsDbfs": -45.0,
                "minimumResidualConsensusToPrimaryDb": -18.0,
                "minimumResidualConsensusSpectralCosine": 0.3,
            },
        )

        self.assertEqual(accepted, [intervals[0]])
        self.assertEqual(report["acceptedIntervalCount"], 1)
        self.assertFalse(report["decisions"][1]["checks"]["secondaryAudible"])
        self.assertFalse(report["decisions"][2]["checks"]["spectralAgreement"])

    def test_pause_aware_reflow_prefers_breath_over_equal_word_counts(self) -> None:
        words = []
        for index in range(10):
            start = 1.0 + index * 0.4
            acoustic_end = start + (0.1 if index == 5 else 0.38)
            words.append(
                {
                    "text": f"w{index + 1}",
                    "start": start,
                    "acousticEnd": acoustic_end,
                    "end": start + 0.39,
                }
            )
        lines, report = reflow_aligned_lyric_lines(
            [
                {"referenceGroup": 1, "syllables": words[:5]},
                {"referenceGroup": 1, "syllables": words[5:]},
            ],
            maximum_words=6,
            target_words=5,
            minimum_words=3,
            natural_pause_seconds=0.18,
        )
        self.assertEqual(
            [line["text"] for line in lines],
            ["w1 w2 w3 w4 w5 w6", "w7 w8 w9 w10"],
        )
        self.assertEqual(report["orphanLineCount"], 0)
        self.assertEqual(report["chosenBreaks"][0]["afterText"], "w6")

    def test_vietnamese_reflow_keeps_semantic_noun_phrase_together(self) -> None:
        texts = ["Tôi", "vẫn", "nhớ", "năm", "xưa", "một", "chiều"]
        words = []
        for index, text in enumerate(texts):
            start = 1.0 + index * 0.4
            words.append(
                {
                    "text": text,
                    "start": start,
                    "acousticEnd": start + 0.38,
                    "end": start + 0.39,
                }
            )
        lines, report = reflow_aligned_lyric_lines(
            [
                {"referenceGroup": 1, "syllables": words[:4]},
                {"referenceGroup": 1, "syllables": words[4:]},
            ],
            maximum_words=6,
            target_words=5,
            minimum_words=3,
            natural_pause_seconds=0.18,
        )
        self.assertEqual(
            [line["text"] for line in lines],
            ["Tôi vẫn nhớ", "năm xưa một chiều"],
        )
        self.assertEqual(report["orphanLineCount"], 0)

    def test_vietnamese_reflow_prefers_complete_vocative_at_comma(self) -> None:
        def aligned_words(text: str) -> list[dict[str, Any]]:
            output = []
            for index, token in enumerate(text.split()):
                start = 1.0 + index * 0.4
                output.append(
                    {
                        "text": token,
                        "start": start,
                        "acousticEnd": start + 0.38,
                        "end": start + 0.39,
                    }
                )
            return output

        for text, expected in (
            (
                "Còn gì đâu em, tháng ngày vui qua mất rồi",
                ["Còn gì đâu em,", "tháng ngày vui qua mất rồi"],
            ),
            (
                "Còn gì đâu em, thôi đừng đến xót xa thêm",
                ["Còn gì đâu em,", "thôi đừng đến xót xa thêm"],
            ),
        ):
            words = aligned_words(text)
            lines, _ = reflow_aligned_lyric_lines(
                [{"referenceGroup": 1, "syllables": words}],
                maximum_words=6,
                target_words=5,
                minimum_words=3,
                natural_pause_seconds=0.18,
            )
            self.assertEqual([line["text"] for line in lines], expected)

    def test_semantic_width_reflow_has_no_hard_word_count(self) -> None:
        text = "một câu trọn nghĩa có bảy từ đây"
        words = []
        for index, token in enumerate(text.split()):
            start = 1.0 + index * 0.4
            words.append(
                {
                    "text": token,
                    "start": start,
                    "acousticEnd": start + 0.38,
                    "end": start + 0.39,
                }
            )
        lines, report = reflow_aligned_lyric_lines(
            [{"referenceGroup": 1, "syllables": words}],
            maximum_words=None,
            target_words=None,
            natural_pause_seconds=0.18,
            measure_text=lambda value: float(len(value) * 10),
            maximum_line_width=1000.0,
        )
        self.assertEqual([line["text"] for line in lines], [text])
        self.assertFalse(report["hardWordLimitUsed"])

    def test_semantic_width_reflow_shrinks_to_keep_phrase_whole(self) -> None:
        text = "Từng câu nói yêu đương ngọt ngào"
        words = []
        for index, token in enumerate(text.split()):
            start = 1.0 + index * 0.4
            words.append(
                {
                    "text": token,
                    "start": start,
                    "acousticEnd": start + 0.38,
                    "end": start + 0.39,
                }
            )
        lines, _ = reflow_aligned_lyric_lines(
            [{"referenceGroup": 1, "syllables": words}],
            maximum_words=None,
            target_words=None,
            natural_pause_seconds=0.18,
            measure_text=lambda value: float(len(value) * 10),
            maximum_line_width=250.0,
            hard_maximum_line_width=400.0,
        )
        self.assertEqual([line["text"] for line in lines], [text])

    def test_semantic_width_reflow_prefers_the_stronger_sung_pause(self) -> None:
        texts = "Đoạn đường ta đi còn dài lê thê".split()
        starts = [1.0, 1.4, 1.8, 2.2, 3.6, 4.0, 4.4, 4.8]
        words = []
        for index, (text, start) in enumerate(zip(texts, starts, strict=True)):
            acoustic_end = start + 0.38
            if text == "ta":
                acoustic_end = start + 0.04
            elif text == "đi":
                acoustic_end = start + 0.06
            words.append(
                {
                    "text": text,
                    "start": start,
                    "acousticEnd": acoustic_end,
                    "end": (
                        starts[index + 1] - 0.02
                        if index + 1 < len(starts)
                        else start + 0.5
                    ),
                }
            )

        lines, report = reflow_aligned_lyric_lines(
            [{"referenceGroup": 1, "syllables": words}],
            maximum_words=None,
            target_words=None,
            natural_pause_seconds=0.18,
            measure_text=lambda value: float(len(value) * 10),
            maximum_line_width=190.0,
        )

        self.assertEqual(
            [line["text"] for line in lines],
            ["Đoạn đường ta đi", "còn dài lê thê"],
        )
        self.assertEqual(report["chosenBreaks"][0]["afterText"], "đi")

    def test_semantic_width_reflow_never_crosses_authoritative_punctuation(self) -> None:
        texts = "Tôi vẫn nhớ câu chuyện tình đầu, đã ngủ yên trong cõi thâm sâu".split()
        words = [
            {
                "text": text,
                "start": 1.0 + index * 0.4,
                "acousticEnd": 1.36 + index * 0.4,
                "end": 1.39 + index * 0.4,
            }
            for index, text in enumerate(texts)
        ]
        lines, _ = reflow_aligned_lyric_lines(
            [{"referenceGroup": 1, "syllables": words}],
            maximum_words=None,
            target_words=None,
            natural_pause_seconds=0.18,
            measure_text=lambda value: float(len(value) * 10),
            maximum_line_width=170.0,
            semantic_analyzer=lambda _: {
                "tokens": [
                    {"text": text, "pos": "NOUN", "head": 0, "deprel": "root"}
                    for text in texts
                ],
                "constituents": [],
                "punctuationBoundaries": [],
            },
        )
        comma_line = next(line for line in lines if "," in line["text"])
        self.assertTrue(comma_line["text"].endswith(","))

    def test_semantic_width_reflow_uses_hard_width_instead_of_orphan(self) -> None:
        texts = "Bao năm qua dù xa anh nhưng tôi vẫn nhớ, nhớ con đường nắng u buồn".split()
        words = []
        for index, text in enumerate(texts):
            start = 1.0 + index * 0.4
            words.append(
                {
                    "text": text,
                    "start": start,
                    "acousticEnd": start + 0.36,
                    "end": start + 0.39,
                }
            )

        def analyzer(_: str) -> dict[str, object]:
            return {
                "tokens": [
                    {"text": text, "pos": "NOUN", "head": 0, "deprel": "root"}
                    for text in texts
                ],
                "constituents": [],
                "punctuationBoundaries": [
                    {
                        "boundary": 3,
                        "mark": ",",
                        "probability": 0.8,
                        "margin": 0.5,
                    }
                ],
            }

        lines, _ = reflow_aligned_lyric_lines(
            [{"referenceGroup": 1, "syllables": words}],
            maximum_words=None,
            target_words=None,
            natural_pause_seconds=0.18,
            measure_text=lambda value: float(len(value) * 10),
            maximum_line_width=160.0,
            hard_maximum_line_width=180.0,
            semantic_analyzer=analyzer,
        )

        self.assertTrue(all(len(line["syllables"]) > 1 for line in lines))
        self.assertEqual(" ".join(line["text"] for line in lines), " ".join(texts))

    def test_semantic_width_reflow_reports_an_unfittable_lexical_unit(self) -> None:
        words = [
            {"text": "yêu", "start": 1.0, "acousticEnd": 1.2, "end": 1.3},
            {"text": "đương", "start": 1.4, "acousticEnd": 1.6, "end": 1.7},
        ]
        with self.assertRaisesRegex(
            ValueError, "Unable to fit protected semantic units"
        ):
            reflow_aligned_lyric_lines(
                [{"referenceGroup": 1, "syllables": words}],
                maximum_words=None,
                target_words=None,
                natural_pause_seconds=0.18,
                measure_text=lambda value: float(len(value.split()) * 100),
                maximum_line_width=100.0,
                hard_maximum_line_width=150.0,
                semantic_analyzer=lambda _: {
                    "tokens": [
                        {
                            "text": "yêu đương",
                            "pos": "VERB",
                            "head": 0,
                            "deprel": "root",
                        }
                    ],
                    "constituents": [
                        {"label": "ROOT", "startToken": 0, "endToken": 1}
                    ],
                    "punctuationBoundaries": [],
                },
            )

    def test_speaker_phrase_smoothing_corrects_a_confident_isolated_outlier(self) -> None:
        embeddings = np.array(
            [
                [1.0, 0.0],
                [0.98, 0.02],
                [0.0, 1.0],
                [0.02, 0.98],
                [0.0, 1.0],
                [0.01, 0.99],
            ],
            dtype=np.float32,
        )
        roles, report, _ = cluster_speaker_embeddings(
            embeddings,
            [350.0, 360.0, 180.0, 175.0, 185.0, 190.0],
            [1, 1, 1, 2, 2, 2],
            {
                "minimumSpeakerClusterMeanMargin": 0.1,
                "minimumSpeakerPitchRatio": 1.2,
                "speakerPhraseMajorityRatio": 0.66,
            },
        )
        self.assertEqual(
            roles,
            ["female", "female", "female", "male", "male", "male"],
        )
        self.assertEqual(
            report["phraseMajoritySmoothing"][0]["changedLineIndexes"], [3]
        )
        self.assertEqual(report["lines"][2]["rawRole"], "male")
        self.assertEqual(report["lines"][2]["role"], "female")

    def test_speaker_clustering_fails_closed_below_quality_target(self) -> None:
        embeddings = np.array(
            [
                [1.0, 0.0],
                [0.7, 0.7],
                [0.0, 1.0],
                [0.7, 0.71],
            ],
            dtype=np.float32,
        )
        with self.assertRaisesRegex(ValueError, "Singer-count evidence is ambiguous"):
            cluster_speaker_embeddings(
                embeddings,
                [170.0, 180.0, 330.0, 340.0],
                [1, 2, 3, 4],
                {
                    "minimumSpeakerClusterMeanMargin": 0.5,
                    "minimumSpeakerPitchRatio": 2.5,
                    "failOnAmbiguousSingerCount": True,
                },
            )

    def test_speaker_clustering_keeps_a_solo_voice_as_one_singer(self) -> None:
        embeddings = np.array(
            [[1.0, 0.0], [0.999, 0.01], [0.997, 0.02], [0.995, 0.03]],
            dtype=np.float32,
        )

        roles, report, state = cluster_speaker_embeddings(
            embeddings,
            [178.0, 181.0, 176.0, 183.0],
            [1, 2, 3, 4],
            {
                "adaptiveSpeakerCount": True,
                "failOnAmbiguousSingerCount": True,
                "failOnAmbiguousGender": True,
            },
        )

        self.assertEqual(roles, ["male"] * 4)
        self.assertEqual(report["clusterCount"], 1)
        self.assertEqual(report["speakerCountDecision"]["selected"], 1)
        self.assertEqual(state["centers"].shape[0], 1)

    def test_speaker_clustering_supports_two_same_gender_singers(self) -> None:
        embeddings = np.array(
            [[1.0, 0.0], [0.99, 0.01], [0.0, 1.0], [0.01, 0.99]],
            dtype=np.float32,
        )

        roles, report, _ = cluster_speaker_embeddings(
            embeddings,
            [320.0, 330.0, 360.0, 350.0],
            [1, 2, 3, 4],
            {
                "adaptiveSpeakerCount": True,
                "failOnAmbiguousSingerCount": True,
                "failOnAmbiguousGender": True,
            },
        )

        self.assertEqual(roles, ["female"] * 4)
        self.assertEqual(report["clusterCount"], 2)
        self.assertEqual(set(report["clusterRole"].values()), {"female"})

    def test_speaker_clustering_rejects_ambiguous_gender(self) -> None:
        embeddings = np.array(
            [[1.0, 0.0], [0.999, 0.01], [0.997, 0.02], [0.995, 0.03]],
            dtype=np.float32,
        )

        with self.assertRaisesRegex(ValueError, "Absolute pitch cannot establish"):
            cluster_speaker_embeddings(
                embeddings,
                [250.0, 255.0, 245.0, 252.0],
                [1, 2, 3, 4],
                {
                    "adaptiveSpeakerCount": True,
                    "failOnAmbiguousSingerCount": True,
                    "failOnAmbiguousGender": True,
                },
            )

    def test_backing_identity_candidates_require_articulated_lyrics(self) -> None:
        evidence = [
            {
                "wordCount": 4,
                "matchedWordCount": 4,
                "consonantCount": 5,
                "supportedConsonantCount": 4,
                "meanBackingConsonantConfidence": 0.6,
                "backingToLeadConsonantConfidenceRatio": 0.7,
            },
            {
                "wordCount": 4,
                "matchedWordCount": 4,
                "consonantCount": 5,
                "supportedConsonantCount": 1,
                "meanBackingConsonantConfidence": 0.8,
                "backingToLeadConsonantConfidenceRatio": 0.9,
            },
            {
                "wordCount": 4,
                "matchedWordCount": 1,
                "consonantCount": 5,
                "supportedConsonantCount": 5,
                "meanBackingConsonantConfidence": 0.8,
                "backingToLeadConsonantConfidenceRatio": 0.9,
            },
        ]

        self.assertEqual(select_backing_identity_candidates(evidence, {}), [0])

    def test_same_gender_distinct_speakers_can_form_a_duet(self) -> None:
        decoded, report = decide_colead_word_roles(
            [
                {
                    "wordEvidence": [
                        {
                            "text": text,
                            "leadStart": float(index),
                            "matched": True,
                            "leadWordRole": "female",
                            "backingWordRole": "female",
                            "leadWordCluster": 0,
                            "backingWordCluster": 1,
                            "consonantCount": 2,
                            "consonantCoverage": 1.0,
                            "meanBackingConsonantConfidence": 0.8,
                            "backingToLeadConsonantConfidenceRatio": 0.8,
                        }
                        for index, text in enumerate(("cùng", "hát"))
                    ]
                }
            ],
            {},
        )

        self.assertEqual(decoded, [[True, True]])
        self.assertEqual(report["duetWordCount"], 2)

    def test_two_line_semantic_group_uses_independent_pitch_to_break_a_tie(self) -> None:
        roles = ["male", "female", "female", "male"]
        report = _resolve_short_semantic_group_roles(
            roles,
            [329.0, 369.0, 245.0, 164.0],
            [0.12, 0.06, 0.01, 0.29],
            [4, 4, 18, 18],
            {
                "maleMaximumMedianHz": 235.0,
                "femaleMinimumMedianHz": 275.0,
                "maximumSpeakerAmbiguousPitchResolutionMargin": 0.05,
            },
        )
        self.assertEqual(roles, ["female", "female", "male", "male"])
        self.assertEqual([item["referenceGroup"] for item in report], [4, 18])

    def test_colead_requires_same_lyrics_and_bridges_only_strong_neighbors(self) -> None:
        roles, report = decide_colead_roles(
            ["male", "male", "male", "female"],
            ["female", "male", "female", "male"],
            [
                {
                    "wordCount": 3,
                    "matchedWordCount": 3,
                    "meanOnsetDeltaSeconds": 0.2,
                    "backingToLeadConfidenceRatio": 0.7,
                    "meanLeadConfidence": 0.9,
                    "consonantCount": 4,
                    "supportedConsonantCount": 4,
                    "meanBackingConsonantConfidence": 0.7,
                    "backingToLeadConsonantConfidenceRatio": 0.8,
                },
                {
                    "wordCount": 4,
                    "matchedWordCount": 4,
                    "meanOnsetDeltaSeconds": 0.1,
                    "backingToLeadConfidenceRatio": 0.8,
                    "meanLeadConfidence": 0.9,
                    "consonantCount": 4,
                    "supportedConsonantCount": 4,
                    "meanBackingConsonantConfidence": 0.7,
                    "backingToLeadConsonantConfidenceRatio": 0.8,
                },
                {
                    "wordCount": 3,
                    "matchedWordCount": 3,
                    "meanOnsetDeltaSeconds": 0.2,
                    "backingToLeadConfidenceRatio": 0.7,
                    "meanLeadConfidence": 0.9,
                    "consonantCount": 4,
                    "supportedConsonantCount": 4,
                    "meanBackingConsonantConfidence": 0.7,
                    "backingToLeadConsonantConfidenceRatio": 0.8,
                },
                {
                    "wordCount": 3,
                    "matchedWordCount": 1,
                    "meanOnsetDeltaSeconds": 0.2,
                    "backingToLeadConfidenceRatio": 0.7,
                    "meanLeadConfidence": 0.9,
                    "consonantCount": 4,
                    "supportedConsonantCount": 4,
                    "meanBackingConsonantConfidence": 0.7,
                    "backingToLeadConsonantConfidenceRatio": 0.8,
                },
            ],
            {},
        )
        self.assertEqual(roles, ["duet", "duet", "duet", "female"])
        self.assertEqual(report["strongCoLeadLines"], [1, 3])
        self.assertEqual(report["bridgedCoLeadLines"], [2])

    def test_colead_continues_across_a_proven_semantic_phrase_tail(self) -> None:
        roles, report = decide_colead_roles(
            ["male", "male", "male"],
            ["female", "female", "male"],
            [
                {
                    "wordCount": 4,
                    "matchedWordCount": 4,
                    "meanOnsetDeltaSeconds": 0.1,
                    "backingToLeadConfidenceRatio": 0.8,
                    "meanLeadConfidence": 0.9,
                    "consonantCount": 4,
                    "supportedConsonantCount": 4,
                    "meanBackingConsonantConfidence": 0.7,
                    "backingToLeadConsonantConfidenceRatio": 0.8,
                },
                {
                    "wordCount": 4,
                    "matchedWordCount": 4,
                    "meanOnsetDeltaSeconds": 0.1,
                    "backingToLeadConfidenceRatio": 0.8,
                    "meanLeadConfidence": 0.9,
                    "consonantCount": 4,
                    "supportedConsonantCount": 4,
                    "meanBackingConsonantConfidence": 0.7,
                    "backingToLeadConsonantConfidenceRatio": 0.8,
                },
                {
                    "wordCount": 3,
                    "matchedWordCount": 3,
                    "meanOnsetDeltaSeconds": 0.1,
                    "backingToLeadConfidenceRatio": 0.8,
                    "meanLeadConfidence": 0.9,
                    "consonantCount": 4,
                    "supportedConsonantCount": 4,
                    "meanBackingConsonantConfidence": 0.7,
                    "backingToLeadConsonantConfidenceRatio": 0.8,
                },
            ],
            {},
            reference_groups=[7, 7, 7],
        )
        self.assertEqual(roles, ["duet", "duet", "duet"])
        self.assertEqual(report["phraseContinuationCoLeadLines"], [3])

    def test_colead_rejects_vowels_without_consonant_articulation(self) -> None:
        roles, report = decide_colead_roles(
            ["male"],
            ["female"],
            [
                {
                    "wordCount": 3,
                    "matchedWordCount": 3,
                    "meanOnsetDeltaSeconds": 0.2,
                    "backingToLeadConfidenceRatio": 0.7,
                    "meanLeadConfidence": 0.9,
                    "consonantCount": 6,
                    "supportedConsonantCount": 2,
                    "meanBackingConsonantConfidence": 0.18,
                    "backingToLeadConsonantConfidenceRatio": 0.3,
                }
            ],
            {},
        )
        self.assertEqual(roles, ["male"])
        self.assertEqual(report["duetLineCount"], 0)

    def test_word_colead_decoder_finds_boundaries_inside_lines(self) -> None:
        def word(
            text: str,
            start: float,
            *,
            matched: bool,
            lead: str,
            backing: str,
        ) -> dict[str, object]:
            return {
                "text": text,
                "leadStart": start,
                "matched": matched,
                "leadWordRole": lead,
                "backingWordRole": backing,
                "consonantCount": 1,
                "consonantCoverage": 1.0,
                "meanBackingConsonantConfidence": 0.7,
                "backingToLeadConsonantConfidenceRatio": 0.8,
            }

        decoded, report = decide_colead_word_roles(
            [
                {
                    "wordEvidence": [
                        word("Cau", 1.0, matched=True, lead="female", backing="female"),
                        word("ca", 1.4, matched=True, lead="female", backing="female"),
                        word("dao", 1.8, matched=True, lead="female", backing="female"),
                        word("me", 2.2, matched=True, lead="female", backing="male"),
                        word("ru", 2.6, matched=False, lead="female", backing="male"),
                    ]
                },
                {
                    "wordEvidence": [
                        word("con", 3.0, matched=False, lead="male", backing="female"),
                        word("bao", 3.4, matched=False, lead="male", backing="female"),
                        word("nam", 3.8, matched=False, lead="male", backing="female"),
                        word("van", 4.2, matched=True, lead="male", backing="female"),
                        word("nho", 4.6, matched=False, lead="male", backing="female"),
                    ]
                },
                {
                    "wordEvidence": [
                        word("nuoc", 5.0, matched=True, lead="male", backing="female"),
                        word("non", 5.4, matched=True, lead="male", backing="female"),
                        word("nay", 5.8, matched=True, lead="male", backing="female"),
                    ]
                },
                {
                    "wordEvidence": [
                        word("nguoi", 6.5, matched=True, lead="female", backing="female")
                    ]
                },
            ],
            {},
        )
        self.assertEqual(decoded[0], [False, False, False, True, True])
        self.assertEqual(decoded[1], [True, True, True, True, True])
        self.assertEqual(decoded[2], [True, True, True])
        self.assertEqual(decoded[3], [False])
        self.assertEqual(report["decodedRanges"][0]["startText"], "me")
        self.assertEqual(report["decodedRanges"][0]["endText"], "nay")

    def test_foreground_gate_rejects_a_late_backing_harmony_entry(self) -> None:
        decoded = [[True] * 4, [True] * 4]
        lexical = [
            {
                "wordEvidence": [
                    {
                        "backingForegroundRmsDbfs": -62.0,
                        "backingToLeadForegroundRmsDb": -35.0,
                    }
                    for _ in range(4)
                ]
            },
            {
                "wordEvidence": [
                    {
                        "backingForegroundRmsDbfs": -25.0,
                        "backingToLeadForegroundRmsDb": -3.0,
                    }
                    for _ in range(4)
                ]
            },
        ]
        report = gate_colead_groups_by_foreground_prominence(
            decoded, lexical, [7, 7], {}
        )
        self.assertEqual(decoded, [[False] * 4, [False] * 4])
        self.assertEqual(report["duetWordCountAfterGate"], 0)
        self.assertEqual(
            report["rejectedBackingHarmonyGroups"][0]["referenceGroup"], 7
        )

    def test_foreground_gate_keeps_phrase_wide_coleads(self) -> None:
        decoded = [[True] * 4, [True] * 4]
        lexical = [
            {
                "wordEvidence": [
                    {
                        "backingForegroundRmsDbfs": -24.0,
                        "backingToLeadForegroundRmsDb": -2.5,
                    }
                    for _ in range(4)
                ]
            },
            {
                "wordEvidence": [
                    {
                        "backingForegroundRmsDbfs": -27.0,
                        "backingToLeadForegroundRmsDb": -5.0,
                    }
                    for _ in range(4)
                ]
            },
        ]
        report = gate_colead_groups_by_foreground_prominence(
            decoded, lexical, [7, 7], {}
        )
        self.assertEqual(decoded, [[True] * 4, [True] * 4])
        self.assertEqual(report["duetWordCountAfterGate"], 8)
        self.assertEqual(report["acceptedGroups"][0]["referenceGroup"], 7)

    def test_foreground_gate_extends_a_localized_confirmation_to_lexical_boundaries(
        self,
    ) -> None:
        decoded = [
            [False] * 3,
            [True] * 3,
            [True] * 4,
            [True] * 3,
            [True] * 3,
        ]
        prominent = {(2, 2), (3, 0), (3, 1), (3, 2), (4, 2)}
        lexical = []
        for line_index, line in enumerate(decoded):
            lexical.append(
                {
                    "wordEvidence": [
                        {
                            "text": f"w{line_index}-{word_index}",
                            "backingForegroundRmsDbfs": (
                                -24.0
                                if (line_index, word_index) in prominent
                                else -34.0
                            ),
                            "backingToLeadForegroundRmsDb": (
                                -3.0
                                if (line_index, word_index) in prominent
                                else -12.0
                            ),
                        }
                        for word_index in range(len(line))
                    ]
                }
            )

        report = gate_colead_groups_by_foreground_prominence(
            decoded, lexical, [17] * len(decoded), {}
        )

        self.assertEqual(decoded[0], [False] * 3)
        self.assertEqual(decoded[1], [True] * 3)
        self.assertEqual(decoded[2], [True] * 4)
        self.assertEqual(decoded[3], [True] * 3)
        self.assertEqual(decoded[4], [True] * 3)
        self.assertEqual(report["duetWordCountAfterGate"], 13)
        localized = report["localizedAcceptedGroups"][0]
        self.assertEqual(localized["referenceGroup"], 17)
        self.assertEqual(localized["segments"][0]["foregroundWordCount"], 5)
        self.assertEqual(localized["segments"][0]["wordCount"], 13)
        self.assertEqual(
            localized["segments"][0]["foregroundEvidenceWordCount"], 8
        )

    def test_foreground_gate_accepts_a_fully_prominent_short_unseeded_run(
        self,
    ) -> None:
        decoded = [[False, True, True]]
        lexical = [
            {
                "wordEvidence": [
                    {
                        "text": "mong",
                        "backingForegroundRmsDbfs": -80.0,
                        "backingToLeadForegroundRmsDb": -60.0,
                    },
                    {
                        "text": "chung",
                        "backingForegroundRmsDbfs": -22.0,
                        "backingToLeadForegroundRmsDb": -5.0,
                    },
                    {
                        "text": "doi",
                        "backingForegroundRmsDbfs": -23.0,
                        "backingToLeadForegroundRmsDb": -4.0,
                    },
                ]
            }
        ]

        report = gate_colead_groups_by_foreground_prominence(
            decoded,
            lexical,
            [18],
            {},
            foreground_verifiable_unseeded_ranges=[
                {
                    "referenceGroup": 18,
                    "startLine": 1,
                    "startWord": 2,
                    "endLine": 1,
                    "endWord": 3,
                }
            ],
        )

        self.assertEqual(decoded, [[False, True, True]])
        localized = report["localizedAcceptedGroups"][0]
        self.assertEqual(localized["referenceGroup"], 18)
        self.assertEqual(
            localized["segments"][0]["confirmationPolicy"],
            "unseeded-word-speaker-plus-full-foreground-consensus",
        )

    def test_role_boundary_splits_a_display_line_at_the_word(self) -> None:
        lines, report = split_lines_on_syllable_roles(
            [
                {
                    "index": 1,
                    "slot": "top",
                    "role": "female",
                    "roleEvidence": "speaker-lexical-inference",
                    "referenceGroup": 5,
                    "syllables": [
                        {"text": "Cau", "start": 1.0, "end": 1.3},
                        {"text": "ca", "start": 1.4, "end": 1.7},
                        {"text": "dao", "start": 1.8, "end": 2.1},
                        {"text": "me", "start": 2.2, "end": 2.5},
                        {"text": "ru", "start": 2.6, "end": 2.9},
                    ],
                }
            ],
            [
                {
                    "wordEvidence": [
                        {"coLead": False},
                        {"coLead": False},
                        {"coLead": False},
                        {"coLead": True},
                        {"coLead": True},
                    ]
                }
            ],
        )
        self.assertEqual([line["text"] for line in lines], ["Cau ca dao", "me ru"])
        self.assertEqual([line["role"] for line in lines], ["female", "duet"])
        self.assertEqual([line["slot"] for line in lines], ["top", "bottom"])
        self.assertEqual(report["roleBoundarySplitCount"], 1)

    def test_short_solo_fragment_next_to_duet_keeps_its_acoustic_role(self) -> None:
        lines, report = split_lines_on_syllable_roles(
            [
                {
                    "index": 1,
                    "slot": "top",
                    "role": "male",
                    "roleEvidence": "speaker-lexical-inference",
                    "referenceGroup": 18,
                    "syllables": [
                        {"text": "mong", "start": 1.0, "end": 1.4},
                        {"text": "chung", "start": 1.42, "end": 1.8},
                        {"text": "doi", "start": 1.82, "end": 2.2},
                    ],
                }
            ],
            [
                {
                    "wordEvidence": [
                        {"coLead": False},
                        {"coLead": True},
                        {"coLead": True},
                    ]
                }
            ],
        )

        self.assertEqual(
            [(line["text"], line["role"]) for line in lines],
            [("mong", "male"), ("chung doi", "duet")],
        )
        self.assertEqual(report["roleBoundarySplitCount"], 1)
        self.assertEqual(report["cueRoleConsolidations"], [])

    def test_role_split_keeps_unfinished_clause_owner_across_display_line(self) -> None:
        def syllables(words: tuple[str, ...], start: float) -> list[dict[str, object]]:
            return [
                {"text": text, "start": start + i * 0.4, "end": start + i * 0.4 + 0.3}
                for i, text in enumerate(words)
            ]

        lines, report = split_lines_on_syllable_roles(
            [
                {
                    "index": 1,
                    "slot": "top",
                    "role": "female",
                    "referenceGroup": 18,
                    "syllables": syllables(("Nguoi", "ra", "di", "mai"), 1.0),
                },
                {
                    "index": 2,
                    "slot": "bottom",
                    "role": "male",
                    "referenceGroup": 18,
                    "syllables": syllables(("mai,", "mong", "chung", "doi"), 3.0),
                },
            ],
            [
                {
                    "wordEvidence": [
                        {"text": text, "leadWordRole": "female", "coLead": False}
                        for text in ("Nguoi", "ra", "di", "mai")
                    ]
                },
                {
                    "wordEvidence": [
                        {"text": "mai,", "leadWordRole": "female", "coLead": False},
                        {"text": "mong", "leadWordRole": "male", "coLead": True},
                        {"text": "chung", "leadWordRole": "male", "coLead": True},
                        {"text": "doi", "leadWordRole": "male", "coLead": True},
                    ]
                },
            ],
        )
        self.assertEqual(
            [(line["text"], line["role"]) for line in lines],
            [
                ("Nguoi ra di mai", "female"),
                ("mai,", "female"),
                ("mong chung doi", "duet"),
            ],
        )
        self.assertEqual(len(report["semanticClauseCarryovers"]), 1)
        self.assertEqual(
            report["semanticClauseCarryovers"][0]["throughText"], "mai,"
        )

    def test_word_colead_decoder_rejects_an_unseeded_word_run(self) -> None:
        evidence = [
            {
                "wordEvidence": [
                    {
                        "text": text,
                        "leadStart": start,
                        "matched": True,
                        "leadWordRole": "male",
                        "backingWordRole": "female",
                        "consonantCount": 2,
                        "consonantCoverage": 1.0,
                        "meanBackingConsonantConfidence": 0.7,
                        "backingToLeadConsonantConfidenceRatio": 0.8,
                    }
                    for text, start in (("one", 1.0), ("two", 1.4))
                ]
            }
        ]
        decoded, report = decide_colead_word_roles(
            evidence, {}, seed_lines=[False]
        )
        self.assertEqual(decoded, [[False, False]])
        self.assertEqual(report["rejectedUnseededRangeCount"], 1)

    def test_sung_clause_role_does_not_change_for_a_transient_overlap(self) -> None:
        decoded = [[False, True, True, False]]
        lines = [
            {
                "syllables": [
                    {"text": text, "start": index * 0.4, "acousticEnd": index * 0.4 + 0.3, "end": index * 0.4 + 0.38}
                    for index, text in enumerate(("mong", "chung", "doi", "mai"))
                ]
            }
        ]

        report = regularize_colead_to_sung_clause_boundaries(
            decoded, lines, [[0, 0, 0, 0]], {}
        )

        self.assertEqual(decoded, [[False, False, False, False]])
        self.assertEqual(report[0]["status"], "demoted-transient-overlap")

    def test_sung_clause_majority_is_promoted_only_inside_that_clause(self) -> None:
        decoded = [[False, False, True, True, True, True]]
        lines = [
            {
                "syllables": [
                    {"text": text, "start": index * 0.4, "acousticEnd": index * 0.4 + 0.3, "end": index * 0.4 + 0.38}
                    for index, text in enumerate(("cau,", "ca", "me", "ru", "con", "nho"))
                ]
            }
        ]

        report = regularize_colead_to_sung_clause_boundaries(
            decoded, lines, [[0, 0, 1, 1, 1, 1]], {}
        )

        self.assertEqual(decoded, [[False, False, True, True, True, True]])
        self.assertEqual(report, [])

    def test_acoustic_pause_can_prove_a_rare_intra_clause_role_boundary(self) -> None:
        decoded = [[False, False, True, True]]
        lines = [
            {
                "syllables": [
                    {"text": "a", "start": 0.0, "acousticEnd": 0.3, "end": 0.38},
                    {"text": "b", "start": 0.4, "acousticEnd": 0.7, "end": 0.78},
                    {"text": "c", "start": 1.0, "acousticEnd": 1.3, "end": 1.38},
                    {"text": "d", "start": 1.4, "acousticEnd": 1.7, "end": 1.78},
                ]
            }
        ]

        report = regularize_colead_to_sung_clause_boundaries(
            decoded, lines, [[0, 0, 0, 0]], {}
        )

        self.assertEqual(decoded, [[False, False, True, True]])
        self.assertEqual(report[0]["status"], "preserved-acoustic-boundary")

    def test_opposite_gender_consensus_promotes_the_complete_sung_clause(self) -> None:
        decoded = [[False, False], [False, False]]
        evidence = [
            {
                "wordEvidence": [
                    {
                        "matched": True,
                        "consonantCount": 2,
                        "supportedConsonantCount": 2,
                        "leadForegroundRmsDbfs": -19.0,
                        "backingForegroundRmsDbfs": -20.0,
                        "backingToLeadForegroundRmsDb": -1.0,
                    }
                    for _ in range(2)
                ]
            },
            {
                "wordEvidence": [
                    {
                        "matched": True,
                        "consonantCount": 2,
                        "supportedConsonantCount": 2,
                        "leadForegroundRmsDbfs": -20.0,
                        "backingForegroundRmsDbfs": -19.0,
                        "backingToLeadForegroundRmsDb": 1.0,
                    }
                    for _ in range(2)
                ]
            },
        ]

        report = promote_opposite_gender_colead_clauses(
            decoded, evidence, [[0, 0], [0, 0]], {}
        )

        self.assertEqual(decoded, [[True, True], [True, True]])
        self.assertEqual(report[0]["changedWordCount"], 4)
        self.assertEqual(
            report[0]["policy"],
            "independent-male-female-full-clause-consensus",
        )

    def test_opposite_gender_consensus_rejects_a_solo_dominant_stem(self) -> None:
        decoded = [[False, False, False]]
        evidence = [
            {
                "wordEvidence": [
                    {
                        "matched": True,
                        "consonantCount": 2,
                        "supportedConsonantCount": 2,
                        "leadForegroundRmsDbfs": -18.0,
                        "backingForegroundRmsDbfs": -55.0,
                        "backingToLeadForegroundRmsDb": -37.0,
                    }
                    for _ in range(3)
                ]
            }
        ]

        report = promote_opposite_gender_colead_clauses(
            decoded, evidence, [[0, 0, 0]], {}
        )

        self.assertEqual(decoded, [[False, False, False]])
        self.assertEqual(report, [])

    def test_word_colead_decoder_can_defer_an_unseeded_run_to_foreground_gate(
        self,
    ) -> None:
        evidence = [
            {
                "wordEvidence": [
                    {
                        "text": text,
                        "leadStart": start,
                        "matched": True,
                        "consonantCount": 1,
                        "consonantCoverage": 1.0,
                        "meanBackingConsonantConfidence": 0.8,
                        "backingToLeadConsonantConfidenceRatio": 0.8,
                        "leadWordRole": "male",
                        "backingWordRole": "female",
                        "leadWordCluster": 0,
                        "backingWordCluster": 1,
                    }
                    for text, start in (("chung", 1.0), ("doi", 1.4))
                ]
            }
        ]
        decoded, report = decide_colead_word_roles(
            evidence,
            {"allowForegroundVerifiedUnseededCoLeadRanges": True},
            seed_lines=[False],
        )
        self.assertEqual(decoded, [[True, True]])
        self.assertEqual(report["rejectedUnseededRangeCount"], 0)
        self.assertEqual(report["foregroundVerifiableUnseededRangeCount"], 1)
        self.assertFalse(report["decodedRanges"][0]["lineSeeded"])

    def test_intra_line_role_boundary_keeps_three_adaptive_count_in_dots(self) -> None:
        plan = build_karaoke_render_plan(
            {
                "lines": [
                    {
                        "index": 1,
                        "slot": "top",
                        "role": "female",
                        "start": 2.0,
                        "end": 2.4,
                        "syllables": [
                            {"text": "solo", "start": 2.0, "end": 2.4}
                        ],
                    },
                    {
                        "index": 2,
                        "slot": "bottom",
                        "role": "female",
                        "start": 2.2,
                        "end": 2.5,
                        "syllables": [
                            {"text": "solo2", "start": 2.2, "end": 2.5}
                        ],
                    },
                    {
                        "index": 3,
                        "slot": "top",
                        "role": "duet",
                        "roleEvidence": "word-level-colead-sequence",
                        "start": 2.85,
                        "end": 3.3,
                        "syllables": [
                            {"text": "duet", "start": 2.85, "end": 3.3}
                        ],
                    },
                ]
            },
            {
                "layout": {
                    "displayLeadSeconds": 1.6,
                    "minimumDisplayLeadSeconds": 0.45,
                    "minimumVisualSweepSeconds": 0.0,
                    "maximumWordsPerScreen": 10,
                },
                "roleChangeCue": {
                    "enabled": True,
                    "transitionOnly": True,
                    "dotCount": 3,
                    "durationSeconds": 3.2,
                    "minimumDurationSeconds": 1.2,
                    "minimumIntraPhraseDurationSeconds": 0.35,
                    "requiredOnEveryTransition": True,
                    "requiredDotCount": 3,
                },
            },
        )
        self.assertTrue(plan["events"][2]["showRoleCue"])
        self.assertAlmostEqual(plan["events"][2]["effectiveLeadSeconds"], 0.37)
        self.assertFalse(plan["errors"])

    def test_seeded_duet_extends_through_a_supported_semantic_tail(self) -> None:
        def evidence(text: str, start: float, matched: bool) -> dict[str, object]:
            return {
                "text": text,
                "leadStart": start,
                "matched": matched,
                "leadWordRole": "male",
                "backingWordRole": "female",
                "consonantCount": 2,
                "consonantCoverage": 1.0,
                "meanBackingConsonantConfidence": 0.7,
                "backingToLeadConsonantConfidenceRatio": 0.8,
            }

        decoded, report = decide_colead_word_roles(
            [
                {
                    "wordEvidence": [
                        evidence("Mong", 1.0, True),
                        evidence("chung", 1.4, True),
                        evidence("doi", 1.8, True),
                    ]
                },
                {
                    "wordEvidence": [
                        evidence("con", 2.2, False),
                        evidence("chia", 2.6, False),
                        evidence("phoi", 3.0, False),
                    ]
                },
            ],
            {},
            seed_lines=[True, False],
            reference_groups=[9, 9],
        )
        self.assertEqual(decoded, [[True, True, True], [True, True, True]])
        self.assertEqual(
            report["semanticTailExtensions"][0]["fromText"], "con"
        )
        self.assertFalse(report["ambiguousSemanticTails"])

    def test_later_phrase_seed_extends_an_earlier_word_anchor_run(self) -> None:
        def evidence(
            text: str, start: float, *, matched: bool, opposite: bool = True
        ) -> dict[str, object]:
            return {
                "text": text,
                "leadStart": start,
                "matched": matched,
                "leadWordRole": "male",
                "backingWordRole": "female" if opposite else "male",
                "consonantCount": 2,
                "consonantCoverage": 1.0,
                "meanBackingConsonantConfidence": 0.7,
                "backingToLeadConsonantConfidenceRatio": 0.8,
            }

        decoded, report = decide_colead_word_roles(
            [
                {
                    "wordEvidence": [
                        evidence("Mong", 1.0, matched=False, opposite=False),
                        evidence("chung", 1.4, matched=True),
                        evidence("doi", 1.8, matched=True),
                    ]
                },
                {
                    "wordEvidence": [
                        evidence("van", 2.2, matched=False),
                        evidence("con", 2.6, matched=False),
                        evidence("chia", 3.0, matched=False),
                        evidence("phoi", 3.4, matched=False),
                    ]
                },
            ],
            {},
            seed_lines=[False, True],
            reference_groups=[9, 9],
        )
        self.assertEqual(decoded, [[True] * 3, [True] * 4])
        self.assertTrue(report["decodedRanges"][0]["semanticSeedExtended"])

    def test_colead_transition_preserves_exact_word_boundary(self) -> None:
        decoded = [[False, False, False, True], [True] * 4, [True] * 3]
        lexical = [
            {
                "wordEvidence": [
                    {"text": text} for text in ("Cau", "ca", "dao", "me")
                ]
            },
            {"wordEvidence": [{"text": text} for text in ("ru", "con", "bao", "nam")]},
            {"wordEvidence": [{"text": text} for text in ("nuoc", "non", "nay")]},
        ]
        report = _extend_colead_across_speaker_transition(
            decoded,
            lexical,
            ["female", "male", "male"],
            ["female", "female", "female"],
            [False, True, False],
            [5, 5, 5],
        )
        self.assertEqual(decoded[0], [False, False, False, True])
        self.assertEqual(decoded[1:], [[True] * 4, [True] * 3])
        self.assertEqual(report[0]["startText"], "me")
        self.assertEqual(report[0]["boundarySource"], "existing-word-evidence")
        self.assertEqual(report[0]["changedWordCount"], 0)

    def test_colead_transition_starts_after_punctuation_before_anchor(self) -> None:
        decoded = [[False] * 6, [False] * 4, [False] * 4]

        def word(
            text: str, *, matched: bool = False, distinct: bool = False
        ) -> dict[str, object]:
            return {
                "text": text,
                "matched": matched,
                "leadWordRole": "male" if distinct else "female",
                "backingWordRole": "female",
                "leadWordCluster": 1 if distinct else 0,
                "backingWordCluster": 0,
                "consonantCount": 2,
                "consonantCoverage": 1.0,
                "meanBackingConsonantConfidence": 0.7,
                "backingToLeadConsonantConfidenceRatio": 0.8,
            }

        lexical = [
            {
                "wordEvidence": [
                    word(text)
                    for text in ("Nguoi", "ra", "di", "con", "di", "mai")
                ]
            },
            {
                "wordEvidence": [
                    word("mai,"),
                    word("mong"),
                    word("chung", matched=True, distinct=True),
                    word("doi", matched=True, distinct=True),
                ]
            },
            {
                "wordEvidence": [
                    word(text, distinct=True)
                    for text in ("van", "con", "chia", "phoi")
                ]
            },
        ]
        report = _extend_colead_across_speaker_transition(
            decoded,
            lexical,
            ["female", "male", "male"],
            ["female", "female", "female"],
            [False, False, True],
            [5, 5, 5],
        )
        self.assertEqual(decoded[0], [False] * 6)
        self.assertEqual(decoded[1], [False, True, True, True])
        self.assertEqual(decoded[2], [True] * 4)
        self.assertEqual(report[0]["transitionAfterLine"], 1)
        self.assertEqual(report[0]["startText"], "mong")
        self.assertEqual(
            report[0]["boundarySource"], "punctuation-before-word-anchor"
        )

    def test_colead_transition_without_word_anchor_stays_solo(self) -> None:
        decoded = [[False] * 2, [False] * 2]
        lexical = [
            {"wordEvidence": [{"text": "solo"}, {"text": "phrase,"}]},
            {"wordEvidence": [{"text": "weak"}, {"text": "tail"}]},
        ]
        report = _extend_colead_across_speaker_transition(
            decoded,
            lexical,
            ["female", "male"],
            ["female", "female"],
            [False, True],
            [5, 5],
        )
        self.assertEqual(decoded, [[False] * 2, [False] * 2])
        self.assertEqual(report, [])

    def test_only_proven_speaker_transition_bypasses_phrase_smoothing(self) -> None:
        roles = ["male", "male", "male", "female"]
        report = _restore_proven_speaker_transition_roles(
            roles,
            ["female", "male", "male", "female"],
            [
                {
                    "referenceGroup": 5,
                    "transitionAfterLine": 1,
                    "endLine": 3,
                }
            ],
        )
        self.assertEqual(roles, ["female", "male", "male", "female"])
        self.assertEqual(report[0]["changedLineIndexes"], [1])

    def test_word_majority_reconciles_a_mixed_backing_line_embedding(self) -> None:
        roles, report = _reconcile_backing_roles_from_word_majority(
            ["male", "female"],
            [
                {
                    "wordEvidence": [
                        {"backingWordRole": "male"},
                        {"backingWordRole": "female"},
                        {"backingWordRole": "female"},
                    ]
                },
                {
                    "wordEvidence": [
                        {"backingWordRole": "male"},
                        {"backingWordRole": "female"},
                    ]
                },
            ],
            {
                "minimumSpeakerWordMajorityCount": 3,
                "speakerWordMajorityRatio": 0.66,
            },
        )
        self.assertEqual(roles, ["female", "female"])
        self.assertEqual(report[0]["line"], 1)
        self.assertEqual(report[0]["majorityRatio"], 0.6667)

    def test_strong_seed_and_aggregate_phrase_evidence_fill_a_duet_group(self) -> None:
        decoded = [[False, True, True], [False, False, False, False]]
        report = _extend_seeded_colead_semantic_groups(
            decoded,
            [
                {
                    "wordCount": 3,
                    "matchedWordCount": 3,
                    "consonantCount": 8,
                    "supportedConsonantCount": 8,
                },
                {
                    "wordCount": 4,
                    "matchedWordCount": 2,
                    "consonantCount": 8,
                    "supportedConsonantCount": 3,
                },
            ],
            ["male", "male"],
            ["female", "female"],
            [True, False],
            [27, 27],
            {
                "minimumCoLeadSemanticPhraseCoverage": 0.66,
                "minimumCoLeadSemanticGroupConsonantCoverage": 0.6,
            },
        )
        self.assertEqual(decoded, [[True] * 3, [True] * 4])
        self.assertEqual(report[0]["referenceGroup"], 27)

    def test_partial_semantic_phrase_harmony_does_not_become_duet(self) -> None:
        def evidence(
            text: str, start: float, *, opposite: bool
        ) -> dict[str, object]:
            return {
                "text": text,
                "leadStart": start,
                "matched": opposite,
                "leadWordRole": "male",
                "backingWordRole": "female" if opposite else "male",
                "consonantCount": 2,
                "consonantCoverage": 1.0,
                "meanBackingConsonantConfidence": 0.7,
                "backingToLeadConsonantConfidenceRatio": 0.8,
            }

        first = ["Tôi", "nhớ", "mãi", "năm"]
        tail = ["xưa", "một", "chiều"]
        decoded, report = decide_colead_word_roles(
            [
                {
                    "wordEvidence": [
                        evidence(text, 1.0 + index * 0.4, opposite=True)
                        for index, text in enumerate(first)
                    ]
                },
                {
                    "wordEvidence": [
                        evidence(text, 2.6 + index * 0.4, opposite=False)
                        for index, text in enumerate(tail)
                    ]
                },
            ],
            {"minimumCoLeadSemanticPhraseCoverage": 0.66},
            seed_lines=[True, False],
            reference_groups=[12, 12],
        )
        self.assertEqual(decoded, [[False] * 4, [False] * 3])
        self.assertEqual(len(report["rejectedPartialPhraseRanges"]), 1)
        self.assertEqual(
            report["rejectedPartialPhraseRanges"][0]["startText"], "Tôi"
        )

    def test_lyric_leakage_refinement_is_targeted_and_preserves_reconstruction(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            sample_rate = 16_000
            time = np.arange(sample_rate * 3, dtype=np.float32) / sample_rate
            envelope = ((time >= 0.5) & (time <= 1.5)).astype(np.float32)
            adlib_envelope = ((time >= 2.0) & (time <= 2.5)).astype(np.float32)
            vocals = (
                0.25 * np.sin(2 * np.pi * 220 * time) * envelope
                + 0.25 * np.sin(2 * np.pi * 330 * time) * adlib_envelope
            )
            music = 0.2 * np.sin(2 * np.pi * 880 * time)
            instrumental = music + 0.18 * vocals
            source = np.column_stack((instrumental + vocals, instrumental + vocals))
            instrumental_stereo = np.column_stack((instrumental, instrumental))
            vocal_stereo = np.column_stack((vocals, vocals))
            sf.write(root / "source.wav", source, sample_rate, subtype="FLOAT")
            sf.write(
                root / "instrumental.flac",
                instrumental_stereo,
                sample_rate,
                subtype="PCM_24",
            )
            sf.write(
                root / "vocals.flac", vocal_stereo, sample_rate, subtype="PCM_24"
            )
            report = refine_lyric_leakage(
                root / "instrumental.flac",
                root / "vocals.flac",
                [
                    {
                        "syllables": [
                            {"text": "test", "start": 0.5, "end": 1.5}
                        ]
                    }
                ],
                {
                    "reviewSpectralOverlapDb": -20.0,
                    "residualActivityReviewSpectralOverlapDb": -20.0,
                    "minimumResidualActivityDurationSeconds": 0.08,
                    "maximumSpectralOverlapDb": 0.0,
                    "maximumWordCoherentLeakageDb": 0.0,
                    "maximumResidualActivityCoherentLeakageDb": 0.0,
                    "minimumFlaggedWordImprovementDb": 0.01,
                    "minimumResidualActivityImprovementDb": 0.01,
                    "minimumMusicPreservationSnrDb": 15.0,
                    "refinementFftSize": 1024,
                    "refinementHopSize": 256,
                },
            )
            self.assertEqual(report["status"], "passed")
            self.assertEqual(report["metrics"]["flaggedWordCount"], 1)
            self.assertGreaterEqual(
                report["metrics"]["unalignedResidualActivityIntervalCount"], 1
            )
            self.assertEqual(report["policy"], "remove-fragments")
            self.assertGreater(report["flaggedWords"][0]["improvementDb"], 0)
            self.assertTrue(
                any(
                    interval["unaligned"]
                    and interval["improvementDb"] > 0
                    and interval["coherentImprovementDb"] > 0
                    for interval in report["residualActivityIntervals"]
                )
            )
            self.assertGreater(
                report["metrics"]["untargetedMusicPreservationSnrDb"], 60.0
            )
            reconstructed = stem_separation_qc(
                root / "source.wav",
                root / "instrumental.flac",
                root / "vocals.flac",
                {"minimumReconstructionSnrDb": 60.0},
            )
            self.assertEqual(reconstructed["status"], "passed")

    def test_lyric_leakage_cleans_quiet_words_and_short_boundary_calls(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            sample_rate = 16_000
            time = np.arange(sample_rate * 3, dtype=np.float32) / sample_rate
            word_envelope = ((time >= 0.5) & (time <= 1.5)).astype(np.float32)
            boundary_call = (time <= 0.12).astype(np.float32)
            vocals = (
                0.25 * np.sin(2 * np.pi * 220 * time) * word_envelope
                + 0.25 * np.sin(2 * np.pi * 330 * time) * boundary_call
            )
            music = 0.2 * np.sin(2 * np.pi * 880 * time)
            instrumental = music + 0.03 * vocals
            source = np.column_stack((instrumental + vocals, instrumental + vocals))
            sf.write(root / "source.wav", source, sample_rate, subtype="FLOAT")
            sf.write(
                root / "instrumental.flac",
                np.column_stack((instrumental, instrumental)),
                sample_rate,
                subtype="PCM_24",
            )
            sf.write(
                root / "vocals.flac",
                np.column_stack((vocals, vocals)),
                sample_rate,
                subtype="PCM_24",
            )
            report = refine_lyric_leakage(
                root / "instrumental.flac",
                root / "vocals.flac",
                [
                    {
                        "syllables": [
                            {"text": "quiet", "start": 0.5, "end": 1.5}
                        ]
                    }
                ],
                {
                    "reviewSpectralOverlapDb": -6.0,
                    "residualActivityReviewSpectralOverlapDb": -40.0,
                    "residualActivityReviewCoherentLeakageDb": -50.0,
                    "minimumResidualVocalActivityDb": -50.0,
                    "minimumResidualActivityDurationSeconds": 0.05,
                    "maximumSpectralOverlapDb": -20.0,
                    "maximumResidualActivityCoherentLeakageDb": -20.0,
                    "minimumMusicPreservationSnrDb": 15.0,
                    "refinementFftSize": 1024,
                    "refinementHopSize": 256,
                    "refinementStrength": 1.0,
                    "residualActivityMagnitudeRefinementStrength": 1.0,
                    "vocalDominanceLowerRatio": 0.25,
                    "vocalDominanceRatioSpan": 1.5,
                },
            )
            self.assertEqual(report["status"], "passed")
            self.assertEqual(report["metrics"]["flaggedWordCount"], 0)
            self.assertGreater(report["wordAudit"][0]["improvementDb"], 0)
            self.assertLessEqual(
                report["wordAudit"][0]["postSpectralOverlapDb"], -20.0
            )
            self.assertTrue(
                any(
                    interval["unaligned"]
                    and interval["start"] == 0.0
                    and interval["postSpectralOverlapDb"] <= -20.0
                    for interval in report["residualActivityIntervals"]
                )
            )
            reconstructed = stem_separation_qc(
                root / "source.wav",
                root / "instrumental.flac",
                root / "vocals.flac",
                {"minimumReconstructionSnrDb": 60.0},
            )
            self.assertEqual(reconstructed["status"], "passed")

    def test_lyric_leakage_audit_only_preserves_natural_stems(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            sample_rate = 16_000
            time = np.arange(sample_rate * 2, dtype=np.float32) / sample_rate
            envelope = ((time >= 0.5) & (time <= 1.5)).astype(np.float32)
            vocals = 0.2 * np.sin(2 * np.pi * 220 * time) * envelope
            instrumental = (
                0.2 * np.sin(2 * np.pi * 880 * time) + 0.1 * vocals
            )
            instrumental_stereo = np.column_stack(
                (instrumental, instrumental)
            )
            vocal_stereo = np.column_stack((vocals, vocals))
            sf.write(
                root / "instrumental.flac",
                instrumental_stereo,
                sample_rate,
                subtype="PCM_24",
            )
            sf.write(
                root / "vocals.flac",
                vocal_stereo,
                sample_rate,
                subtype="PCM_24",
            )
            before, _ = sf.read(
                root / "instrumental.flac", dtype="float32", always_2d=True
            )
            report = refine_lyric_leakage(
                root / "instrumental.flac",
                root / "vocals.flac",
                [
                    {
                        "syllables": [
                            {"text": "natural", "start": 0.5, "end": 1.5}
                        ]
                    }
                ],
                {
                    "refinementMode": "audit-only",
                    "reviewSpectralOverlapDb": -40.0,
                    "scanUnalignedVocalActivity": False,
                },
            )
            after, _ = sf.read(
                root / "instrumental.flac", dtype="float32", always_2d=True
            )
            self.assertEqual(report["status"], "passed-with-warnings")
            self.assertTrue(report["metrics"]["naturalStemPreserved"])
            self.assertFalse(report["metrics"]["refinementApplied"])
            np.testing.assert_array_equal(before, after)

    def test_lead_role_policy_ignores_backing_vocal_overlap(self) -> None:
        lines = [
            {"index": 1, "start": 1.0, "end": 3.0},
            {"index": 2, "start": 3.0, "end": 5.0},
        ]
        assigned, report = assign_lead_roles(
            lines,
            {
                "roles": {
                    "semanticPolicy": "lead-lyric-owner",
                    "defaultLeadRole": "male",
                    "ranges": [
                        {
                            "startSeconds": 1.0,
                            "endSeconds": 3.0,
                            "leadRole": "male",
                            "secondaryVocalRole": "female",
                            "secondaryVocalType": "ad-lib",
                        },
                        {
                            "startSeconds": 3.0,
                            "endSeconds": 5.0,
                            "leadRole": "female",
                            "secondaryVocalRole": "male",
                            "secondaryVocalType": "harmony",
                        },
                    ],
                }
            },
            {"duetRequiresCoLeadEvidence": True},
        )
        self.assertEqual([line["role"] for line in assigned], ["male", "female"])
        self.assertFalse(report["backingVocalsAffectRole"])

    def test_duet_role_requires_explicit_co_lead_evidence(self) -> None:
        lines = [{"index": 1, "start": 1.0, "end": 3.0}]
        with self.assertRaisesRegex(ValueError, "coLead=true"):
            assign_lead_roles(
                lines,
                {
                    "roles": {
                        "ranges": [
                            {
                                "startSeconds": 1.0,
                                "endSeconds": 3.0,
                                "leadRole": "duet",
                            }
                        ]
                    }
                },
                {"duetRequiresCoLeadEvidence": True},
            )
        assigned, _ = assign_lead_roles(
            lines,
            {
                "roles": {
                    "ranges": [
                        {
                            "startSeconds": 1.0,
                            "endSeconds": 3.0,
                            "leadRole": "duet",
                            "coLead": True,
                        }
                    ]
                }
            },
            {"duetRequiresCoLeadEvidence": True},
        )
        self.assertEqual(assigned[0]["role"], "duet")

    def test_stem_separation_qc_accepts_consistent_lossless_stems(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            sample_rate = 8_000
            time = np.arange(sample_rate, dtype=np.float32) / sample_rate
            instrumental = 0.2 * np.sin(2 * np.pi * 220 * time)
            vocals = 0.1 * np.sin(2 * np.pi * 330 * time)
            source = instrumental + vocals
            for name, values in {
                "source.wav": source,
                "instrumental.wav": instrumental,
                "vocals.wav": vocals,
            }.items():
                sf.write(root / name, values, sample_rate, subtype="FLOAT")
            report = stem_separation_qc(
                root / "source.wav",
                root / "instrumental.wav",
                root / "vocals.wav",
                {},
            )
            self.assertEqual(report["status"], "passed")
            self.assertGreater(report["metrics"]["reconstructionSnrDb"], 80)

    def test_stem_separation_qc_accepts_full_scale_source_but_not_output_stem(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            sample_rate = 8_000
            source = np.zeros(sample_rate, dtype=np.float32)
            source[0] = 1.0
            instrumental = source * 0.9
            vocals = source - instrumental
            for name, values in {
                "source.wav": source,
                "instrumental.wav": instrumental,
                "vocals.wav": vocals,
            }.items():
                sf.write(root / name, values, sample_rate, subtype="FLOAT")
            accepted = stem_separation_qc(
                root / "source.wav",
                root / "instrumental.wav",
                root / "vocals.wav",
                {"minimumReconstructionSnrDb": 60.0},
            )
            self.assertEqual(accepted["status"], "passed")

            sf.write(root / "instrumental.wav", source, sample_rate, subtype="FLOAT")
            rejected = stem_separation_qc(
                root / "source.wav",
                root / "instrumental.wav",
                root / "vocals.wav",
                {"minimumReconstructionSnrDb": -100.0},
            )
            self.assertTrue(
                any(
                    error["code"] == "STEM_SAMPLE_PEAK_HIGH"
                    and error["stem"] == "instrumental"
                    for error in rejected["errors"]
                )
            )

    def test_render_plan_resolves_same_slot_display_contention_generically(self) -> None:
        template = {
            "layout": {
                "displayLeadSeconds": 1.6,
                "minimumDisplayLeadSeconds": 0.45,
                "preferredPostHoldSeconds": 0.35,
                "slotTransitionGapSeconds": 0.08,
                "maximumWordsPerScreen": 10,
            }
        }
        lyrics = {
            "lines": [
                {
                    "slot": "top",
                    "start": 10.0,
                    "end": 13.1,
                    "syllables": [{"text": "alpha", "start": 10.0, "end": 13.1}],
                },
                {
                    "slot": "bottom",
                    "start": 13.2,
                    "end": 14.4,
                    "syllables": [{"text": "beta", "start": 13.2, "end": 14.4}],
                },
                {
                    "slot": "top",
                    "start": 14.9,
                    "end": 17.0,
                    "syllables": [{"text": "gamma", "start": 14.9, "end": 17.0}],
                },
            ]
        }
        plan = build_karaoke_render_plan(lyrics, template)
        self.assertFalse(plan["errors"])
        first, _, third = plan["events"]
        self.assertLessEqual(first["displayEnd"] + 0.08, third["displayStart"])
        self.assertGreaterEqual(first["displayEnd"], first["vocalEnd"])
        self.assertEqual(plan["metrics"]["sameSlotOverlapCount"], 0)

    def test_intra_phrase_continuation_uses_bounded_short_lead(self) -> None:
        template = {
            "layout": {
                "displayLeadSeconds": 1.6,
                "minimumDisplayLeadSeconds": 0.45,
                "slotTransitionGapSeconds": 0.08,
                "maximumWordsPerScreen": 10,
            },
            "roleChangeCue": {
                "enabled": True,
                "transitionOnly": True,
                "dotCount": 3,
                "minimumIntraPhraseDurationSeconds": 0.35,
            },
        }
        lyrics = {
            "lines": [
                {
                    "slot": "bottom",
                    "role": "duet",
                    "roleEvidence": "word-level-colead-sequence",
                    "start": 10.0,
                    "end": 12.69,
                    "syllables": [{"text": "first", "start": 10.0, "end": 12.69}],
                },
                {
                    "slot": "top",
                    "role": "duet",
                    "roleEvidence": "word-level-colead-sequence",
                    "start": 12.71,
                    "end": 13.16,
                    "syllables": [{"text": "bridge", "start": 12.71, "end": 13.16}],
                },
                {
                    "slot": "bottom",
                    "role": "duet",
                    "roleEvidence": "word-level-colead-sequence",
                    "start": 13.18,
                    "end": 15.8,
                    "syllables": [{"text": "continuation", "start": 13.18, "end": 15.8}],
                },
            ]
        }

        plan = build_karaoke_render_plan(lyrics, template)

        self.assertFalse(plan["errors"])
        self.assertGreaterEqual(plan["events"][2]["effectiveLeadSeconds"], 0.35)
        self.assertLess(plan["events"][2]["effectiveLeadSeconds"], 0.45)

    def test_render_plan_rejects_unschedulable_same_slot_vocals(self) -> None:
        template = {
            "layout": {
                "displayLeadSeconds": 1.0,
                "minimumDisplayLeadSeconds": 0.2,
                "slotTransitionGapSeconds": 0.08,
                "maximumWordsPerScreen": 10,
            }
        }
        lyrics = {
            "lines": [
                {
                    "slot": "top",
                    "start": 1.0,
                    "end": 3.0,
                    "syllables": [{"text": "one", "start": 1.0, "end": 3.0}],
                },
                {
                    "slot": "bottom",
                    "start": 2.0,
                    "end": 2.5,
                    "syllables": [{"text": "two", "start": 2.0, "end": 2.5}],
                },
                {
                    "slot": "top",
                    "start": 2.8,
                    "end": 4.0,
                    "syllables": [{"text": "three", "start": 2.8, "end": 4.0}],
                },
            ]
        }
        plan = build_karaoke_render_plan(lyrics, template)
        self.assertIn("SAME_SLOT_VOCAL_OVERLAP", {item["code"] for item in plan["errors"]})

    def test_render_plan_checks_the_first_event_lead(self) -> None:
        template = {
            "layout": {
                "displayLeadSeconds": 1.0,
                "minimumDisplayLeadSeconds": 0.45,
                "maximumWordsPerScreen": 10,
            }
        }
        lyrics = {
            "lines": [
                {
                    "slot": "top",
                    "start": 0.2,
                    "end": 1.0,
                    "syllables": [{"text": "early", "start": 0.2, "end": 1.0}],
                }
            ]
        }
        plan = build_karaoke_render_plan(lyrics, template)
        self.assertIn("DISPLAY_LEAD_TOO_SHORT", {item["code"] for item in plan["errors"]})

    def test_render_plan_enforces_a_separate_role_cue_lead(self) -> None:
        template = {
            "layout": {
                "displayLeadSeconds": 0.5,
                "minimumDisplayLeadSeconds": 0.3,
                "maximumWordsPerScreen": 10,
            },
            "roleChangeCue": {
                "enabled": True,
                "dotCount": 3,
                "durationSeconds": 2.0,
                "minimumDurationSeconds": 1.2,
            },
        }
        lyrics = {
            "lines": [
                {
                    "slot": "top",
                    "role": "female",
                    "start": 0.8,
                    "end": 1.5,
                    "syllables": [{"text": "cue", "start": 0.8, "end": 1.5}],
                }
            ]
        }
        plan = build_karaoke_render_plan(lyrics, template)
        self.assertIn("ROLE_CUE_LEAD_TOO_SHORT", {item["code"] for item in plan["errors"]})

    def test_render_plan_resumes_solo_role_without_recoloring_short_duet_boundary(self) -> None:
        template = {
            "layout": {
                "displayLeadSeconds": 0.5,
                "minimumDisplayLeadSeconds": 0.3,
                "maximumWordsPerScreen": 10,
            },
            "roleChangeCue": {
                "enabled": True,
                "transitionOnly": True,
                "dotCount": 3,
                "durationSeconds": 2.0,
                "minimumDurationSeconds": 1.2,
                "minimumIntraPhraseDurationSeconds": 0.35,
                "maximumIntraPhraseResumeInterruptionSeconds": 1.2,
                "maximumIntraPhraseResumeGapSeconds": 0.5,
                "requiredOnEveryTransition": True,
                "requiredDotCount": 3,
            },
        }
        lyrics = {
            "lines": [
                {
                    "slot": "top",
                    "role": "male",
                    "roleEvidence": "speaker-lexical-inference",
                    "referenceGroup": 18,
                    "start": 2.0,
                    "end": 2.4,
                    "syllables": [{"text": "mong", "start": 2.0, "end": 2.4}],
                },
                {
                    "slot": "bottom",
                    "role": "duet",
                    "roleEvidence": "word-level-colead-sequence",
                    "referenceGroup": 18,
                    "start": 2.42,
                    "end": 3.1,
                    "syllables": [{"text": "chung doi", "start": 2.42, "end": 3.1}],
                },
                {
                    "slot": "top",
                    "role": "male",
                    "roleEvidence": "speaker-lexical-inference",
                    "referenceGroup": 18,
                    "start": 3.3,
                    "end": 4.2,
                    "syllables": [{"text": "van con", "start": 3.3, "end": 4.2}],
                },
            ]
        }

        plan = build_karaoke_render_plan(lyrics, template)

        self.assertEqual(plan["status"], "passed")
        self.assertTrue(plan["events"][1]["showRoleCue"])
        self.assertFalse(plan["events"][2]["showRoleCue"])
        self.assertEqual(
            plan["events"][2]["roleCueExemptReason"],
            "resume-after-brief-intra-phrase-role",
        )

    def test_render_plan_shows_same_role_cue_after_a_long_pause(self) -> None:
        template = {
            "layout": {
                "displayLeadSeconds": 0.5,
                "minimumDisplayLeadSeconds": 0.3,
                "maximumWordsPerScreen": 10,
            },
            "roleChangeCue": {
                "enabled": True,
                "transitionOnly": True,
                "showAfterPauseSeconds": 4.0,
                "dotCount": 3,
                "durationSeconds": 2.0,
                "minimumDurationSeconds": 1.2,
            },
        }
        lyrics = {
            "lines": [
                {
                    "slot": "top",
                    "role": "female",
                    "start": 1.0,
                    "end": 2.0,
                    "syllables": [{"text": "trước", "start": 1.0, "end": 2.0}],
                },
                {
                    "slot": "bottom",
                    "role": "female",
                    "start": 7.0,
                    "end": 8.0,
                    "syllables": [{"text": "sau", "start": 7.0, "end": 8.0}],
                },
                {
                    "slot": "top",
                    "role": "female",
                    "start": 10.0,
                    "end": 11.0,
                    "syllables": [{"text": "gần", "start": 10.0, "end": 11.0}],
                },
            ]
        }

        plan = build_karaoke_render_plan(lyrics, template)

        self.assertTrue(plan["events"][0]["showRoleCue"])
        self.assertEqual(plan["events"][0]["roleCueReason"], "initial")
        self.assertTrue(plan["events"][1]["showRoleCue"])
        self.assertEqual(plan["events"][1]["roleCueReason"], "long-pause")
        self.assertEqual(plan["events"][1]["previousVocalGapSeconds"], 5.0)
        self.assertFalse(plan["events"][2]["showRoleCue"])
        self.assertIsNone(plan["events"][2]["roleCueReason"])

    def test_visual_smoothing_is_bounded_and_preserves_phrase_anchors(self) -> None:
        template = {
            "layout": {
                "maximumWordsPerScreen": 10,
                "minimumVisualSweepSeconds": 0.28,
                "maximumVisualBoundaryShiftSeconds": 0.08,
                "maximumVisualJoinGapSeconds": 0.12,
            }
        }
        lyrics = {
            "lines": [
                {
                    "slot": "top",
                    "start": 5.0,
                    "end": 6.2,
                    "syllables": [
                        {"text": "short", "start": 5.0, "end": 5.20},
                        {"text": "long", "start": 5.22, "end": 6.2},
                    ],
                }
            ]
        }
        plan = build_karaoke_render_plan(lyrics, template)
        visual = plan["events"][0]["line"]["syllables"]
        self.assertEqual(visual[0]["visualStart"], 5.0)
        self.assertEqual(visual[-1]["visualEnd"], 6.2)
        self.assertGreaterEqual(visual[0]["visualEnd"] - visual[0]["visualStart"], 0.279)
        self.assertLessEqual(abs(visual[0]["visualEnd"] - 5.21), 0.081)

    def test_visual_smoothing_keeps_boundaries_that_already_pass(self) -> None:
        template = {
            "layout": {
                "maximumWordsPerScreen": 10,
                "minimumVisualSweepSeconds": 0.28,
                "maximumVisualBoundaryShiftSeconds": 0.08,
                "maximumVisualJoinGapSeconds": 0.12,
            }
        }
        lyrics = {
            "lines": [
                {
                    "slot": "top",
                    "start": 1.0,
                    "end": 2.2,
                    "syllables": [
                        {"text": "already", "start": 1.0, "end": 1.5},
                        {"text": "smooth", "start": 1.52, "end": 2.2},
                    ],
                }
            ]
        }
        plan = build_karaoke_render_plan(lyrics, template)
        visual = plan["events"][0]["line"]["syllables"]
        self.assertEqual(visual[0]["visualEnd"], 1.51)
        self.assertEqual(plan["metrics"]["maximumVisualBoundaryShiftSeconds"], 0.01)

    def test_ass_has_double_outline_roles_and_sweep_tags(self) -> None:
        template = {
            "referenceResolution": [1920, 1080],
            "layout": {"bottomMargin": 70, "lineGap": 12},
            "font": {"family": "Arial", "bold": True, "sizeAt1080p": 64},
            "unsung": {
                "fill": "#FFFFFF",
                "outerOutline": "#000000",
                "outerOutlineWidth": 6,
                "shadowOffset": 3,
            },
            "sung": {
                "innerOutline": "#FFFFFF",
                "innerOutlineWidth": 2,
                "colors": {
                    "male": "#153CFF",
                    "female": "#F02A2A",
                    "duet": "#FF3D9D",
                },
            },
        }
        lyrics = {
            "lines": [
                {
                    "slot": "top",
                    "start": 2.0,
                    "end": 3.0,
                    "role": "male",
                    "syllables": [
                        {"text": "Xin", "start": 2.0, "end": 2.4},
                        {"text": "làm", "start": 2.5, "end": 3.0},
                    ],
                }
            ]
        }
        document = build_ass_document(lyrics, template)
        self.assertIn("Style: BackTopMale", document)
        self.assertIn("Style: BorderTopMale", document)
        self.assertIn("Style: CoreTopMale", document)
        self.assertIn("&H00FF3C15", document)
        self.assertIn(r"{\an1\pos(96,934)\q2}{\k160}", document)
        self.assertNotIn("♂", document)
        self.assertIn("{\\kf40}Xin", document)
        self.assertIn("{\\kf50}làm", document)
        self.assertEqual(document.count("Dialogue:"), 26)

    def test_ass_builds_outline_from_synchronized_karaoke_copies(self) -> None:
        template = {
            "referenceResolution": [1920, 1080],
            "layout": {"bottomMargin": 64, "lineGap": 28},
            "font": {
                "family": "Be Vietnam Pro Bold",
                "bold": False,
                "sizeAt1080p": 128,
                "scaleX": 96,
            },
            "unsung": {"fill": "#FFFFFF", "outerOutline": "#000000"},
            "sung": {
                "innerOutline": "#FFFFFF",
                "innerOutlineWidth": 3.5,
                "colors": {"male": "#153CFF"},
            },
        }
        lyrics = {
            "lines": [
                {
                    "slot": "top",
                    "role": "male",
                    "start": 2.0,
                    "end": 3.0,
                    "syllables": [
                        {"text": "Xin", "start": 2.0, "end": 2.4},
                        {"text": "làm", "start": 2.5, "end": 3.0},
                    ],
                }
            ]
        }
        document = build_ass_document(lyrics, template)
        self.assertNotIn(r"\clip(", document)
        self.assertNotIn(r"\ko", document)
        self.assertEqual(document.count("{\\kf40}Xin"), 26)
        self.assertEqual(document.count("{\\kf50}làm"), 26)
        self.assertEqual(document.count("Dialogue: 1"), 24)
        self.assertEqual(document.count("Dialogue:"), 26)

    def test_ass_anchors_lines_and_cues_only_on_role_changes(self) -> None:
        template = {
            "referenceResolution": [1920, 1080],
            "layout": {"bottomMargin": 64, "lineGap": 18, "safeAreaPercent": 5},
            "font": {"family": "Arial", "bold": True, "sizeAt1080p": 90},
            "roleChangeCue": {
                "enabled": True,
                "dotCount": 3,
                "durationSeconds": 2.0,
                "dotFontSizeAt1080p": 72,
            },
            "unsung": {"fill": "#FFFFFF", "outerOutline": "#000000"},
            "sung": {
                "innerOutline": "#FFFFFF",
                "colors": {
                    "male": "#153CFF",
                    "female": "#F02A2A",
                    "duet": "#FF3D9D",
                },
            },
        }
        lyrics = {
            "lines": [
                {
                    "slot": "top",
                    "role": "female",
                    "start": 2.0,
                    "end": 3.0,
                    "syllables": [{"text": "Xin", "start": 2.0, "end": 3.0}],
                },
                {
                    "slot": "bottom",
                    "role": "female",
                    "start": 4.0,
                    "end": 5.0,
                    "syllables": [{"text": "làm", "start": 4.0, "end": 5.0}],
                },
                {
                    "slot": "top",
                    "role": "duet",
                    "start": 6.0,
                    "end": 7.0,
                    "syllables": [{"text": "người", "start": 6.0, "end": 7.0}],
                },
            ]
        }
        document = build_ass_document(lyrics, template)
        cue = r"{\fs72}{\kf67}●\h{\kf67}●\h{\kf66}●{\fs90}\h"
        self.assertIn(r"{\an1\pos(96,908)\q2}" + cue, document)
        self.assertIn(r"{\an3\pos(1824,1016)\q2}{\k160}", document)
        # Two role-change lines, each repeated across back + 24 border copies
        # + core; the middle same-role line contains no cue.
        self.assertEqual(document.count("●"), 156)
        self.assertNotIn("♂", document)
        self.assertNotIn("♀", document)

    def test_timing_qc_rejects_more_than_ten_words(self) -> None:
        syllables = [
            {"text": str(index), "start": index * 0.2, "end": index * 0.2 + 0.15}
            for index in range(11)
        ]
        report = karaoke_timing_qc(
            [
                {
                    "index": 1,
                    "start": 0.0,
                    "end": 2.15,
                    "syllables": syllables,
                }
            ],
            maximum_words=10,
            maximum_line_duration=11.0,
        )
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["errors"][0]["code"], "PHRASE_TOO_LONG")

    def test_timing_qc_limits_both_visible_rows_to_ten_words(self) -> None:
        def line(index: int, count: int, start: float) -> dict:
            return {
                "index": index,
                "start": start,
                "end": start + 1.0,
                "syllables": [
                    {
                        "text": str(word),
                        "start": start + word * 0.1,
                        "end": start + word * 0.1 + 0.08,
                    }
                    for word in range(count)
                ],
            }

        report = karaoke_timing_qc(
            [line(1, 6, 0.0), line(2, 5, 2.0)],
            maximum_words=6,
            maximum_screen_words=10,
            maximum_line_duration=11.0,
        )
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["errors"][0]["code"], "SCREEN_TOO_LONG")
        self.assertEqual(report["metrics"]["maximumWordsPerScreen"], 11)

    def test_timing_qc_rejects_unstamped_forced_alignment_words(self) -> None:
        report = karaoke_timing_qc(
            [
                {
                    "index": 1,
                    "start": 1.0,
                    "end": 1.5,
                    "syllables": [
                        {"text": "Xin", "start": 1.0, "end": 1.5}
                    ],
                }
            ],
            maximum_words=6,
            maximum_screen_words=10,
            maximum_line_duration=11.0,
            forced_alignment_diagnostics={
                "wordCount": 1,
                "lowConfidenceWordCount": 0,
                "minimumConfidence": 0.9,
            },
        )
        self.assertEqual(report["status"], "failed")
        self.assertEqual(
            report["errors"][0]["code"], "FORCED_ALIGNMENT_SOURCE_MISSING"
        )

    def test_friendly_delivery_filename_is_portable(self) -> None:
        metadata = {
            "source": {
                "songTitle": "Xin: Làm/Người? Xa Lạ",
                "referenceArtist": "Đan*Nguyên",
            }
        }
        name = friendly_delivery_filename(metadata, __import__("pathlib").Path("x.mp4"))
        self.assertEqual(name, "Xin Làm Người Xa Lạ - Đan Nguyên [Karaoke].mp4")

    def test_friendly_package_filename_is_unique_and_portable(self) -> None:
        metadata = {
            "source": {
                "songTitle": "Xin: Làm/Người? Xa Lạ",
                "referenceArtist": "Đan*Nguyên",
            }
        }
        name = friendly_package_filename(
            metadata,
            Path("x.mp4"),
            "20260816-song-abc123",
        )
        self.assertEqual(
            name,
            "Xin Làm Người Xa Lạ - Đan Nguyên [Karaoke] abc123.lrail",
        )

    def test_full_aac_source_uses_bitstream_copy_delivery(self) -> None:
        plan = _original_audio_delivery_plan(
            {
                "format": {"duration": "180.0"},
                "streams": [
                    {"codec_type": "audio", "codec_name": "aac", "profile": "LC"}
                ],
                "lyricRail": {"trimStartSeconds": 0.0, "trimEndSeconds": 180.0},
            }
        )
        self.assertEqual(plan["mode"], "bitstream-copy")
        self.assertEqual(plan["outputSuffix"], ".m4a")
        self.assertFalse(plan["transcodeOccurred"])

    def test_full_mp3_source_uses_native_mp3_delivery(self) -> None:
        plan = _original_audio_delivery_plan(
            {
                "format": {"duration": "180.0"},
                "streams": [{"codec_type": "audio", "codec_name": "mp3"}],
                "lyricRail": {"trimStartSeconds": 0.0, "trimEndSeconds": 180.0},
            }
        )
        self.assertEqual(plan["mode"], "bitstream-copy")
        self.assertEqual(plan["outputSuffix"], ".mp3")
        self.assertEqual(plan["mediaType"], "audio/mpeg")

    def test_trimmed_aac_source_uses_sample_accurate_fallback(self) -> None:
        plan = _original_audio_delivery_plan(
            {
                "format": {"duration": "180.0"},
                "streams": [{"codec_type": "audio", "codec_name": "aac"}],
                "lyricRail": {"trimStartSeconds": 2.0, "trimEndSeconds": 178.0},
            }
        )
        self.assertEqual(plan["mode"], "aac-fallback")
        self.assertTrue(plan["transcodeOccurred"])
        self.assertIn("sample-accurate", plan["reason"])


if __name__ == "__main__":
    unittest.main()
