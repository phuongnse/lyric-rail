from __future__ import annotations

import hashlib
import json
from pathlib import Path
from unittest.mock import patch

from lyricrail.revision_alignment import align_revision_scope


def _timing() -> dict[str, object]:
    return {
        "lines": [
            {
                "text": "Câu đầu",
                "start": 1.0,
                "end": 2.0,
                "syllables": [
                    {"text": "Câu", "start": 1.0, "end": 1.4},
                    {"text": "đầu", "start": 1.4, "end": 2.0},
                ],
            },
            {
                "text": "Xin chao",
                "start": 3.0,
                "end": 4.0,
                "syllables": [
                    {"text": "Xin", "start": 3.0, "end": 3.4},
                    {"text": "chao", "start": 3.4, "end": 4.0},
                ],
            },
        ],
        "authoritativeLyrics": {},
        "alignmentDiagnostics": {},
    }


def test_revision_realigns_only_changed_scope_and_hashes_exact_text(tmp_path: Path) -> None:
    audio = tmp_path / "original.m4a"
    timing = tmp_path / "timing.json"
    lyrics = tmp_path / "lyrics.txt"
    output = tmp_path / "output.json"
    audio.write_bytes(b"audio")
    timing.write_text(json.dumps(_timing()), encoding="utf-8")
    exact = "Câu đầu\r\nXin chào\r\n"
    lyrics.write_bytes(exact.encode("utf-8"))

    def aligned(_root, _audio, lines, _settings, **kwargs):
        assert kwargs["target_line_indexes"] == {1}
        lines[1]["syllables"][1].update({"start": 3.55, "end": 4.1})
        lines[1]["start"] = 3.0
        lines[1]["end"] = 4.1
        return lines, {"targetLineIndexes": [1], "minimumConfidence": 0.9}

    with (
        patch("lyricrail.revision_alignment.load_project_config", return_value={"pipeline": {"lyrics": {}}}),
        patch("lyricrail.revision_alignment.force_align_song_lines", side_effect=aligned),
    ):
        report = align_revision_scope(tmp_path, audio, timing, lyrics, output)

    revised = json.loads(output.read_text(encoding="utf-8"))
    assert report["changedLineIndexes"] == [1]
    assert revised["lines"][0]["syllables"][0]["start"] == 1.0
    assert revised["lines"][1]["syllables"][1]["start"] == 3.55
    assert revised["authoritativeLyrics"]["sha256"] == hashlib.sha256(
        exact.encode("utf-8")
    ).hexdigest()


def test_whitespace_only_revision_keeps_timing_without_loading_aligner(tmp_path: Path) -> None:
    audio = tmp_path / "original.m4a"
    timing = tmp_path / "timing.json"
    lyrics = tmp_path / "lyrics.txt"
    output = tmp_path / "output.json"
    audio.write_bytes(b"audio")
    timing.write_text(json.dumps(_timing()), encoding="utf-8")
    lyrics.write_text("  Câu đầu  \nXin chao\n", encoding="utf-8", newline="")
    with patch("lyricrail.revision_alignment.force_align_song_lines") as aligner:
        report = align_revision_scope(tmp_path, audio, timing, lyrics, output)
    aligner.assert_not_called()
    assert report["changedLineIndexes"] == []
    revised = json.loads(output.read_text(encoding="utf-8"))
    assert revised["lines"][0]["text"] == "  Câu đầu  "
    assert revised["lines"][0]["syllables"][0]["start"] == 1.0
