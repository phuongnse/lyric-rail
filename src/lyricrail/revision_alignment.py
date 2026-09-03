from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from .config import load_project_config
from .job import atomic_write_json
from .lyric_input import load_authoritative_lyrics
from .song_alignment import force_align_song_lines


MAXIMUM_TIMING_BYTES = 16 * 1024 * 1024


def align_revision_scope(
    root: Path,
    audio_path: Path,
    timing_path: Path,
    lyrics_path: Path,
    output_path: Path,
) -> dict[str, Any]:
    """Re-align only explicitly changed lines against packaged reference audio."""
    for path, label in (
        (audio_path, "reference audio"),
        (timing_path, "timing payload"),
        (lyrics_path, "authoritative lyrics"),
    ):
        if not path.is_file():
            raise ValueError(f"Revision {label} is missing: {path}")
    if timing_path.stat().st_size > MAXIMUM_TIMING_BYTES:
        raise ValueError("Revision timing payload exceeds 16 MiB")

    timing = json.loads(timing_path.read_text(encoding="utf-8"))
    if not isinstance(timing, dict) or not isinstance(timing.get("lines"), list):
        raise ValueError("Revision timing payload has no lyric lines")
    authoritative = load_authoritative_lyrics(lyrics_path)
    exact_lines = list(authoritative.lines)
    current_lines = timing["lines"]
    if len(exact_lines) != len(current_lines):
        raise ValueError(
            "This edit changes the sung line structure; reprocess the original local media"
        )

    candidate = deepcopy(current_lines)
    changed: set[int] = set()
    for index, (line, exact_text) in enumerate(zip(candidate, exact_lines)):
        if not isinstance(line, dict) or not isinstance(line.get("syllables"), list):
            raise ValueError("Revision timing line has no syllables")
        current_text = str(line.get("text", ""))
        if current_text.split() == exact_text.split():
            continue
        words = exact_text.split()
        if len(words) != len(line["syllables"]):
            raise ValueError(
                "This edit changes a sung word boundary; reprocess the original local media"
            )
        changed.add(index)
        line["text"] = exact_text
        for syllable, word in zip(line["syllables"], words):
            if not isinstance(syllable, dict):
                raise ValueError("Revision timing syllable is invalid")
            syllable["text"] = word

    diagnostics: dict[str, Any] = {
        "mode": "no-acoustic-change",
        "targetLineIndexes": [],
    }
    if changed:
        settings = load_project_config(root)["pipeline"].get("lyrics", {})
        candidate, diagnostics = force_align_song_lines(
            root,
            audio_path,
            candidate,
            settings,
            trusted_timing_endpoints=True,
            target_line_indexes=changed,
        )

    for index, exact_text in enumerate(exact_lines):
        candidate[index]["text"] = exact_text
    digest = hashlib.sha256(authoritative.text.encode("utf-8")).hexdigest()
    word_count = sum(len(line.split()) for line in exact_lines)
    timing["lines"] = candidate
    timing["lineCount"] = len(exact_lines)
    authoritative_metadata = timing.setdefault("authoritativeLyrics", {})
    authoritative_metadata.update(
        {
            "sha256": digest,
            "lineCount": len(exact_lines),
            "wordCount": word_count,
        }
    )
    timing.setdefault("alignmentDiagnostics", {})["inputSha256"] = digest
    timing["revisionAlignment"] = {
        "mode": "affected-scope-acoustic-realignment" if changed else "text-identical",
        "changedLineIndexes": sorted(changed),
        "diagnostics": diagnostics,
    }
    atomic_write_json(output_path, timing)
    return {
        "kind": "lyricrail.revision-alignment",
        "output": str(output_path.resolve()),
        "sha256": digest,
        "lineCount": len(exact_lines),
        "wordCount": word_count,
        "changedLineIndexes": sorted(changed),
    }
