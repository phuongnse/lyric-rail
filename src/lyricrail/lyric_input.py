from __future__ import annotations

import hashlib
import unicodedata
from dataclasses import dataclass
from pathlib import Path


MAXIMUM_LYRIC_BYTES = 1_000_000


@dataclass(frozen=True)
class AuthoritativeLyrics:
    source_path: Path
    text: str
    lines: tuple[str, ...]
    sha256: str

    @property
    def word_count(self) -> int:
        return sum(len(line.split()) for line in self.lines)


def normalize_authoritative_lyrics(text: str) -> tuple[str, tuple[str, ...]]:
    """Validate user-supplied lyrics without changing their words or punctuation."""
    if "\x00" in text:
        raise ValueError("Lyrics must not contain NUL characters.")
    normalized = unicodedata.normalize("NFC", text.replace("\r\n", "\n").replace("\r", "\n"))
    lines = tuple(" ".join(line.split()) for line in normalized.split("\n") if line.strip())
    if not lines:
        raise ValueError("Lyrics file contains no lyric lines.")
    if not any(any(character.isalpha() for character in token) for line in lines for token in line.split()):
        raise ValueError("Lyrics must contain sung words.")
    return "\n".join(lines) + "\n", lines


def load_authoritative_lyrics(path: Path) -> AuthoritativeLyrics:
    source = path.expanduser().resolve()
    if not source.is_file():
        raise ValueError(f"Lyrics file not found: {source}")
    if source.stat().st_size > MAXIMUM_LYRIC_BYTES:
        raise ValueError(
            f"Lyrics file exceeds the {MAXIMUM_LYRIC_BYTES}-byte safety limit: {source}"
        )
    try:
        raw = source.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError(f"Lyrics file must be UTF-8: {source}") from exc
    text, lines = normalize_authoritative_lyrics(raw)
    return AuthoritativeLyrics(
        source_path=source,
        text=text,
        lines=lines,
        sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
    )
