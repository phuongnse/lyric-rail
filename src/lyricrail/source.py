from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .config import resolve_data_root


VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".mpg", ".mpeg"}
AUDIO_EXTENSIONS = {
    ".wav",
    ".flac",
    ".mp3",
    ".m4a",
    ".aac",
    ".ogg",
    ".opus",
    ".wma",
    ".aiff",
    ".aif",
}
MEDIA_EXTENSIONS = VIDEO_EXTENSIONS | AUDIO_EXTENSIONS


@dataclass(frozen=True)
class ResolvedSource:
    input_value: str
    path: Path
    origin: str
    media_kind_hint: str
    requested_start_seconds: float | None = None
    requested_end_seconds: float | None = None
    media_trim_start_seconds: float | None = None
    media_trim_end_seconds: float | None = None
    source_pretrimmed: bool = False
    song_title: str | None = None
    artist: str | None = None
    composer: str | None = None


def parse_timecode(value: str) -> float:
    """Parse seconds, MM:SS, or HH:MM:SS into non-negative seconds."""
    text = value.strip()
    if not text:
        raise ValueError("Timecode must not be empty.")
    parts = text.split(":")
    try:
        numbers = [float(part) for part in parts]
    except ValueError as exc:
        raise ValueError(f"Invalid timecode: {value}") from exc
    if len(numbers) not in {1, 2, 3} or any(number < 0 for number in numbers):
        raise ValueError("Use non-negative seconds, MM:SS, or HH:MM:SS.")
    if len(numbers) > 1 and numbers[-1] >= 60:
        raise ValueError("Seconds must be below 60.")
    if len(numbers) == 3 and numbers[-2] >= 60:
        raise ValueError("Minutes must be below 60.")
    seconds = (
        numbers[0]
        if len(numbers) == 1
        else numbers[0] * 60 + numbers[1]
        if len(numbers) == 2
        else numbers[0] * 3600 + numbers[1] * 60 + numbers[2]
    )
    return round(seconds, 3)


def validate_time_range(
    start_seconds: float | None, end_seconds: float | None
) -> tuple[float | None, float | None]:
    if start_seconds is not None and start_seconds < 0:
        raise ValueError("--start must not be negative.")
    if end_seconds is not None and end_seconds <= 0:
        raise ValueError("--end must be greater than zero.")
    if end_seconds is not None and end_seconds <= (start_seconds or 0.0):
        raise ValueError("--end must be later than --start.")
    return start_seconds, end_seconds


def media_kind_from_path(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in VIDEO_EXTENSIONS:
        return "video"
    if suffix in AUDIO_EXTENSIONS:
        return "audio"
    raise ValueError(f"Unsupported media format: {suffix or '(none)'}")


def resolve_source(
    root: Path,
    value: str | None,
    *,
    start_seconds: float | None = None,
    end_seconds: float | None = None,
    title_override: str | None = None,
    artist_override: str | None = None,
    composer_override: str | None = None,
) -> ResolvedSource:
    """Resolve exactly one regular local media file.

    Cloud objects are intentionally absent from the production pipeline. They
    are authenticated and streamed only by the Player.
    """
    start_seconds, end_seconds = validate_time_range(start_seconds, end_seconds)
    if value and "://" in value:
        raise ValueError("Karaoke processing accepts local disk media only.")
    if value:
        path = Path(value).expanduser().resolve()
        if not path.is_file():
            raise ValueError(f"Media source not found: {path}")
    else:
        input_directory = resolve_data_root(root) / "input"
        candidates = sorted(
            path
            for path in input_directory.iterdir()
            if path.is_file() and path.suffix.lower() in MEDIA_EXTENSIONS
        )
        if len(candidates) != 1:
            raise ValueError(
                "input/ must contain exactly one supported local media file."
            )
        path = candidates[0].resolve()
    return ResolvedSource(
        input_value=str(path),
        path=path,
        origin="local",
        media_kind_hint=media_kind_from_path(path),
        requested_start_seconds=start_seconds,
        requested_end_seconds=end_seconds,
        media_trim_start_seconds=start_seconds,
        media_trim_end_seconds=end_seconds,
        song_title=title_override.strip() if title_override else None,
        artist=artist_override.strip() if artist_override else None,
        composer=composer_override.strip() if composer_override else None,
    )
