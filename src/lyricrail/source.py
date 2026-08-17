from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
import os
import shutil
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse
from urllib.request import Request, urlopen

from .config import resolve_data_root, resolve_environment_path


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
DOWNLOAD_USER_AGENT = "Mozilla/5.0 (compatible; LyricRail/0.5)"


def _source_download_template(cache_root: Path, range_key: str) -> str:
    return str(cache_root / f"%(extractor)s-%(id)s{range_key}" / "source.%(ext)s")


@dataclass(frozen=True)
class ResolvedSource:
    input_value: str
    path: Path
    origin: str
    media_kind_hint: str
    webpage_url: str | None = None
    requested_start_seconds: float | None = None
    requested_end_seconds: float | None = None
    media_trim_start_seconds: float | None = None
    media_trim_end_seconds: float | None = None
    source_pretrimmed: bool = False
    song_title: str | None = None
    artist: str | None = None


def parse_timecode(value: str) -> float:
    """Parse seconds, MM:SS, or HH:MM:SS into non-negative seconds."""
    text = value.strip()
    if not text:
        raise ValueError("Timecode must not be empty.")
    parts = text.split(":")
    if len(parts) == 1:
        try:
            seconds = float(parts[0])
        except ValueError as exc:
            raise ValueError(f"Invalid timecode: {value}") from exc
    elif len(parts) in {2, 3}:
        try:
            numbers = [float(part) for part in parts]
        except ValueError as exc:
            raise ValueError(f"Invalid timecode: {value}") from exc
        if any(number < 0 for number in numbers):
            raise ValueError("Timecode components must not be negative.")
        if numbers[-1] >= 60 or (len(numbers) == 3 and numbers[-2] >= 60):
            raise ValueError("Minutes and seconds components must be below 60.")
        seconds = (
            numbers[0] * 60 + numbers[1]
            if len(numbers) == 2
            else numbers[0] * 3600 + numbers[1] * 60 + numbers[2]
        )
    else:
        raise ValueError("Use seconds, MM:SS, or HH:MM:SS.")
    if seconds < 0:
        raise ValueError("Timecode must not be negative.")
    return round(seconds, 3)


def validate_time_range(
    start_seconds: float | None, end_seconds: float | None
) -> tuple[float | None, float | None]:
    if start_seconds is not None and start_seconds < 0:
        raise ValueError("--start must not be negative.")
    if end_seconds is not None and end_seconds <= 0:
        raise ValueError("--end must be greater than zero.")
    effective_start = start_seconds or 0.0
    if end_seconds is not None and end_seconds <= effective_start:
        raise ValueError("--end must be later than --start.")
    return start_seconds, end_seconds


def is_http_url(value: str) -> bool:
    parsed = urlparse(value.strip())
    return parsed.scheme.lower() in {"http", "https"} and bool(parsed.netloc)


def media_kind_from_path(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in VIDEO_EXTENSIONS:
        return "video"
    if suffix in AUDIO_EXTENSIONS:
        return "audio"
    raise ValueError(f"Unsupported media format: {suffix or '(none)'}")


def _safe_identity(value: str, fallback: str) -> str:
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', " ", value)
    value = re.sub(r"\s+", " ", value).strip(" .")
    return value[:160].rstrip(" .") or fallback


def _strip_video_decorations(value: str) -> str:
    decoration = re.compile(
        r"\s*[\[(](?:official\s+)?(?:music\s+)?(?:video|audio|mv|lyric(?:s)?(?:\s+video)?|"
        r"karaoke|visualizer|hd|4k|remaster(?:ed)?)(?:[^\])}]*)[\])]\s*$",
        flags=re.IGNORECASE,
    )
    previous = ""
    while value != previous:
        previous = value
        value = decoration.sub("", value).strip()
    return value


def _online_identity(info: dict[str, Any], fallback: str) -> tuple[str, str]:
    track = str(info.get("track") or "").strip()
    artist = str(info.get("artist") or info.get("creator") or "").strip()
    if track and artist:
        return _strip_video_decorations(track), artist

    raw_title = str(info.get("title") or fallback).strip()
    uploader = str(info.get("uploader") or info.get("channel") or "").strip()
    for separator in (" - ", " – ", " — "):
        if separator not in raw_title:
            continue
        left, right = (part.strip() for part in raw_title.split(separator, 1))
        if uploader and left.casefold() == uploader.casefold():
            return _strip_video_decorations(right), uploader
    return _strip_video_decorations(raw_title), uploader


def _write_download_sidecar(
    path: Path,
    info: dict[str, Any],
    source_url: str,
    *,
    start_seconds: float | None = None,
    end_seconds: float | None = None,
    title_override: str | None = None,
    artist_override: str | None = None,
) -> None:
    inferred_title, inferred_artist = _online_identity(info, path.stem)
    title = title_override.strip() if title_override else inferred_title
    artist = artist_override.strip() if artist_override is not None else inferred_artist
    payload: dict[str, Any] = {
        "schemaVersion": 1,
        "song": {"title": title, "artist": artist},
        "source": {
            "origin": "url",
            "inputUrl": source_url,
            "webpageUrl": str(info.get("webpage_url") or source_url),
            "extractor": str(info.get("extractor_key") or info.get("extractor") or ""),
            "id": str(info.get("id") or ""),
            "range": {
                "startSeconds": start_seconds,
                "endSeconds": end_seconds,
            },
        },
    }
    sidecar = path.with_suffix(".lyricrail.json")
    sidecar.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def _direct_media_url(
    root: Path,
    source_url: str,
    *,
    download: bool,
    start_seconds: float | None,
    end_seconds: float | None,
    title_override: str | None,
    artist_override: str | None,
) -> ResolvedSource:
    parsed = urlparse(source_url)
    suffix = Path(parsed.path).suffix.lower()
    kind = media_kind_from_path(Path("source" + suffix))
    identity_source = (
        f"{source_url}\n{start_seconds}\n{end_seconds}\n"
        f"{title_override}\n{artist_override}"
    )
    identity = hashlib.sha256(identity_source.encode("utf-8")).hexdigest()[:16]
    folder = resolve_data_root(root) / "cache" / "sources" / f"direct-{identity}"
    raw_title = unquote(Path(parsed.path).stem) or "online-source"
    inferred_title = _safe_identity(
        re.sub(r"[-_]+", " ", raw_title), "online-source"
    )
    title = _safe_identity(title_override or inferred_title, "online-source")
    artist = _safe_identity(artist_override or "", "")
    friendly = f"{title} - {artist}" if artist else title
    path = folder / f"{friendly} [source]{suffix}"
    if download and not (path.is_file() and path.stat().st_size > 100_000):
        folder.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".partial")
        temporary.unlink(missing_ok=True)
        request = Request(source_url, headers={"User-Agent": DOWNLOAD_USER_AGENT})
        try:
            with urlopen(request, timeout=90) as response, temporary.open("wb") as handle:  # noqa: S310
                while chunk := response.read(1024 * 1024):
                    handle.write(chunk)
            if temporary.stat().st_size < 100_000:
                raise RuntimeError("Direct media download was unexpectedly small.")
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
        _write_download_sidecar(
            path,
            {"title": title, "webpage_url": source_url, "extractor": "direct"},
            source_url,
            start_seconds=start_seconds,
            end_seconds=end_seconds,
            title_override=title,
            artist_override=artist,
        )
    return ResolvedSource(
        source_url,
        path,
        "url",
        kind,
        source_url,
        start_seconds,
        end_seconds,
        start_seconds,
        end_seconds,
        False,
        title,
        artist,
    )


def _download_url(
    root: Path,
    source_url: str,
    *,
    download: bool,
    start_seconds: float | None,
    end_seconds: float | None,
    title_override: str | None,
    artist_override: str | None,
) -> ResolvedSource:
    if Path(urlparse(source_url).path).suffix.lower() in MEDIA_EXTENSIONS:
        return _direct_media_url(
            root,
            source_url,
            download=download,
            start_seconds=start_seconds,
            end_seconds=end_seconds,
            title_override=title_override,
            artist_override=artist_override,
        )
    try:
        from yt_dlp import YoutubeDL
        from yt_dlp.utils import download_range_func
    except ImportError as exc:
        raise RuntimeError(
            "URL input requires yt-dlp. Install LyricRail with the media extra."
        ) from exc

    cache_root = resolve_data_root(root) / "cache" / "sources"
    range_requested = start_seconds is not None or end_seconds is not None
    effective_start = float(start_seconds or 0.0)
    range_key = ""
    if range_requested:
        end_key = f"{end_seconds:.3f}" if end_seconds is not None else "end"
        range_key = f"-range-{effective_start:.3f}-{end_key}"
    options: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "format": "bestvideo*+bestaudio/best",
        "merge_output_format": "mp4",
        "windowsfilenames": True,
        "outtmpl": _source_download_template(cache_root, range_key),
        "writeinfojson": download,
    }
    # Modern YouTube delivery may reject a plain Python HTTP fingerprint even
    # when metadata extraction succeeds.  curl_cffi gives yt-dlp a real browser
    # TLS profile; the JavaScript runtime handles current player challenges.
    try:
        from yt_dlp.networking.impersonate import ImpersonateTarget
        import curl_cffi  # noqa: F401

        options["impersonate"] = ImpersonateTarget.from_str(
            "chrome-136:macos-15"
        )
    except ImportError:
        pass
    if shutil.which("node"):
        options["js_runtimes"] = {"node": {}}
        options["remote_components"] = {"ejs:npm"}
    ffmpeg_path = resolve_environment_path("LYRICRAIL_FFMPEG", root, "ffmpeg")
    if ffmpeg_path.is_file():
        options["ffmpeg_location"] = str(ffmpeg_path.parent)
        # yt-dlp's partial-download preflight currently checks FFmpeg through a
        # class method that does not receive the YoutubeDL options. Make the
        # bundled executable discoverable to that check as well. This is
        # platform-neutral: os.pathsep is ';' on Windows and ':' on POSIX.
        path_entries = os.environ.get("PATH", "").split(os.pathsep)
        ffmpeg_dir = str(ffmpeg_path.parent)
        if ffmpeg_dir not in path_entries:
            os.environ["PATH"] = os.pathsep.join((ffmpeg_dir, *path_entries))
    if range_requested:
        options["download_ranges"] = download_range_func(
            [], [(effective_start, end_seconds)]
        )
        options["socket_timeout"] = 30
        options["external_downloader_args"] = {
            "ffmpeg_i": ["-rw_timeout", "30000000"]
        }
        # Stream-copy the requested section. The final production render is the
        # single quality-controlled encode, avoiding a lossy intermediate encode.
        options["force_keyframes_at_cuts"] = False
    range_materialized = range_requested
    try:
        with YoutubeDL(options) as ydl:
            info = ydl.extract_info(source_url, download=download)
    except Exception as range_exc:
        if not (download and range_requested):
            raise RuntimeError(
                f"Unable to resolve media URL with yt-dlp: {range_exc}"
            ) from range_exc
        # Some CDNs reject or stall FFmpeg's ranged request even though a normal
        # yt-dlp download is available. Fall back to the full, lossless source;
        # downstream stages will trim it at the requested timestamps. This keeps
        # the CLI reliable without introducing an intermediate lossy encode.
        fallback_options = dict(options)
        fallback_options.pop("download_ranges", None)
        fallback_options.pop("force_keyframes_at_cuts", None)
        fallback_options.pop("external_downloader_args", None)
        # A failed ranged attempt may have left a valid pretrimmed file in its
        # range-specific cache folder. Reusing that outtmpl for the full fallback
        # makes yt-dlp accept the old clip while we mark it as untrimmed, causing
        # downstream stages to apply the requested timestamps a second time.
        fallback_options["outtmpl"] = _source_download_template(cache_root, "")
        try:
            with YoutubeDL(fallback_options) as ydl:
                info = ydl.extract_info(source_url, download=True)
        except Exception as fallback_exc:
            raise RuntimeError(
                "Unable to download either the requested range or the full "
                f"fallback source with yt-dlp: {fallback_exc}"
            ) from fallback_exc
        range_materialized = False
    if info is None:
        raise RuntimeError(f"yt-dlp returned no media information for: {source_url}")
    if "entries" in info:
        entries = [item for item in info.get("entries") or [] if item]
        if len(entries) != 1:
            raise ValueError("SOURCE must resolve to exactly one song, not a playlist.")
        info = entries[0]

    prepared = Path(ydl.prepare_filename(info)).resolve()
    folder = prepared.parent
    if download:
        candidates = sorted(
            (
                item
                for item in folder.iterdir()
                if item.is_file() and item.suffix.lower() in MEDIA_EXTENSIONS
            ),
            key=lambda item: item.stat().st_size,
            reverse=True,
        )
        if not candidates:
            raise RuntimeError(f"Downloaded media file was not found in: {folder}")
        path = candidates[0]
        raw_title, raw_artist = _online_identity(info, "source")
        if title_override:
            raw_title = title_override
        if artist_override is not None:
            raw_artist = artist_override
        title = _safe_identity(raw_title, "source")
        artist = _safe_identity(raw_artist, "")
        friendly = f"{title} - {artist}" if artist else title
        friendly_path = path.with_name(f"{friendly} [source]{path.suffix.lower()}")
        if friendly_path != path:
            if friendly_path.exists():
                path.unlink()
            else:
                path.replace(friendly_path)
            path = friendly_path
        _write_download_sidecar(
            path,
            info,
            source_url,
            start_seconds=effective_start if range_requested else None,
            end_seconds=end_seconds,
            title_override=title,
            artist_override=artist,
        )
    else:
        raw_title, raw_artist = _online_identity(info, "online-source")
        if title_override:
            raw_title = title_override
        if artist_override is not None:
            raw_artist = artist_override
        title = _safe_identity(raw_title, "online-source")
        artist = _safe_identity(raw_artist, "")
        suffix = prepared.suffix if prepared.suffix.lower() in MEDIA_EXTENSIONS else ".mp4"
        friendly = f"{title} - {artist}" if artist else title
        path = folder / f"{friendly} [source]{suffix}"

    kind = "video" if info.get("vcodec") not in {None, "none"} else "audio"
    materialized_duration = (
        float(end_seconds - effective_start)
        if range_materialized and end_seconds is not None
        else None
    )
    return ResolvedSource(
        input_value=source_url,
        path=path,
        origin="url",
        media_kind_hint=kind,
        webpage_url=str(info.get("webpage_url") or source_url),
        requested_start_seconds=(effective_start if range_requested else None),
        requested_end_seconds=end_seconds,
        media_trim_start_seconds=(
            0.0 if range_materialized else effective_start if range_requested else None
        ),
        media_trim_end_seconds=(
            materialized_duration
            if range_materialized
            else end_seconds if range_requested else None
        ),
        source_pretrimmed=range_materialized,
        song_title=title,
        artist=artist,
    )


def resolve_source(
    root: Path,
    value: str | None,
    *,
    download_urls: bool = True,
    start_seconds: float | None = None,
    end_seconds: float | None = None,
    title_override: str | None = None,
    artist_override: str | None = None,
) -> ResolvedSource:
    """Resolve one URL or local audio/video file into a pipeline media source."""
    start_seconds, end_seconds = validate_time_range(start_seconds, end_seconds)
    if value and is_http_url(value):
        return _download_url(
            root,
            value,
            download=download_urls,
            start_seconds=start_seconds,
            end_seconds=end_seconds,
            title_override=title_override,
            artist_override=artist_override,
        )

    if value:
        path = Path(value).expanduser().resolve()
        if not path.is_file():
            raise ValueError(f"Media source not found: {path}")
        return ResolvedSource(
            input_value=value,
            path=path,
            origin="local",
            media_kind_hint=media_kind_from_path(path),
            requested_start_seconds=start_seconds,
            requested_end_seconds=end_seconds,
            media_trim_start_seconds=start_seconds,
            media_trim_end_seconds=end_seconds,
            song_title=title_override.strip() if title_override else None,
            artist=(artist_override.strip() if artist_override is not None else None),
        )

    input_directory = resolve_data_root(root) / "input"
    candidates = sorted(
        path
        for path in input_directory.iterdir()
        if path.is_file() and path.suffix.lower() in MEDIA_EXTENSIONS
    )
    if not candidates:
        raise ValueError("No supported audio or video file was found in input/.")
    if len(candidates) > 1:
        names = ", ".join(path.name for path in candidates)
        raise ValueError(f"input/ must contain exactly one media source. Found: {names}")
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
        artist=(artist_override.strip() if artist_override is not None else None),
    )
