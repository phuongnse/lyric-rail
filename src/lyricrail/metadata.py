from __future__ import annotations

import re
from pathlib import Path
from typing import Any


SEPARATORS = (" - ", " – ", " — ")


def parse_media_identity(media: Path) -> tuple[str, str]:
    """Apply one conservative filename rule; never infer lyric text."""
    stem = re.sub(r"\s+", " ", media.stem).strip()
    for separator in SEPARATORS:
        if separator in stem:
            title, artist = stem.split(separator, 1)
            return title.strip(), artist.strip()
    return stem, ""


def build_local_metadata(
    media: Path,
    *,
    song_title: str | None = None,
    artist: str | None = None,
    composer: str | None = None,
) -> dict[str, Any]:
    inferred_title, inferred_artist = parse_media_identity(media)
    title = song_title.strip() if song_title else inferred_title
    reference_artist = artist.strip() if artist is not None else inferred_artist
    resolved_composer = composer.strip() if composer else ""
    warnings: list[str] = []
    if not reference_artist:
        warnings.append(
            "The reference artist is unknown; use metadata or 'Song - Artist.ext'."
        )
    return {
        "source": {
            "mediaFile": str(media.resolve()),
            "identityMethod": "command" if song_title or artist or composer else "filename",
            "songTitle": title,
            "referenceArtist": reference_artist,
            "composer": resolved_composer,
        },
        "warnings": warnings,
    }
