from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


SEPARATORS = (" - ", " – ", " — ")


def parse_video_identity(video: Path) -> tuple[str, str]:
    """Infer song title and reference artist from a conservative filename rule."""
    stem = re.sub(r"\s*\[source\]\s*$", "", video.stem, flags=re.IGNORECASE)
    stem = re.sub(r"\s+", " ", stem).strip()
    for separator in SEPARATORS:
        if separator in stem:
            song, artist = stem.split(separator, 1)
            return song.strip(), artist.strip()
    return stem, ""


def _sidecar_identity(video: Path) -> tuple[str, str] | None:
    candidates = [video.with_suffix(".lyricrail.json")]
    clean_stem = re.sub(
        r"\s*\[source\]\s*$", "", video.stem, flags=re.IGNORECASE
    )
    candidates.append(video.with_name(clean_stem + ".lyricrail.json"))
    for candidate in dict.fromkeys(candidates):
        if not candidate.is_file():
            continue
        try:
            payload = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        song = payload.get("song", {})
        title = str(song.get("title", "")).strip()
        artist = str(song.get("artist", "")).strip()
        if title:
            return title, artist
    return None


def _render(template: str, values: dict[str, str]) -> str:
    try:
        return template.format_map(values)
    except KeyError as exc:
        raise ValueError(f"Metadata template is missing variable: {exc.args[0]}") from exc


def _clean_multiline(text: str) -> str:
    text = "\n".join(line.rstrip() for line in text.splitlines())
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit].rstrip(" -–—|,.;:")


def _build_tags(
    templates: list[str], values: dict[str, str], character_limit: int
) -> list[str]:
    tags: list[str] = []
    seen: set[str] = set()
    used = 0

    for template in templates:
        tag = _render(template, values).strip()
        if not tag or tag.casefold() in seen:
            continue
        extra = len(tag) + (1 if tags else 0)
        if used + extra > character_limit:
            break
        tags.append(tag)
        seen.add(tag.casefold())
        used += extra
    return tags


def build_youtube_metadata(
    video: Path,
    channel: dict[str, Any],
    rules: dict[str, Any],
    *,
    song_title: str | None = None,
    artist: str | None = None,
) -> dict[str, Any]:
    sidecar_identity = _sidecar_identity(video)
    inferred_title, inferred_artist = sidecar_identity or parse_video_identity(video)
    resolved_title = song_title.strip() if song_title else inferred_title
    resolved_artist = artist.strip() if artist is not None else inferred_artist
    values = {
        "songTitle": resolved_title,
        "artist": resolved_artist,
        "rightsNotice": str(channel.get("rightsNotice", "")).strip(),
        "descriptionFooter": str(channel.get("descriptionFooter", "")).strip(),
        "channelDisplayName": str(channel.get("channelDisplayName", "")).strip(),
        "channelHandle": str(channel.get("channelHandle", "")).strip(),
        "contactEmail": str(channel.get("contactEmail", "")).strip(),
    }

    if resolved_artist:
        title_template = rules["titleTemplate"]
        description_template = rules["descriptionTemplate"]
    else:
        title_template = rules["titleTemplateWithoutArtist"]
        description_template = rules["descriptionTemplateWithoutArtist"]

    limits = rules.get("limits", {})
    title_limit = int(limits.get("titleCharacters", 100))
    description_limit = int(limits.get("descriptionCharacters", 5000))
    tags_limit = int(limits.get("tagsCharacters", 500))

    title = _truncate(_render(title_template, values), title_limit)
    description = _truncate(
        _clean_multiline(_render(description_template, values)), description_limit
    )
    tags = _build_tags(list(rules.get("tags", [])), values, tags_limit)

    warnings: list[str] = []
    if not resolved_artist:
        warnings.append(
            "The reference artist could not be inferred. Name the file 'Song - Artist.ext'."
        )
    if not channel.get("channelDisplayName"):
        warnings.append("channelDisplayName is not set in config/channel.json.")
    if not channel.get("defaultPlaylistId"):
        warnings.append("defaultPlaylistId is not set; the playlist stage will be skipped.")

    privacy = str(channel.get("defaultPrivacy", "private"))
    if privacy not in {"private", "unlisted", "public"}:
        raise ValueError("defaultPrivacy must be private, unlisted, or public")

    return {
        "source": {
            "videoFile": str(video.resolve()),
            "identityMethod": (
                "command"
                if song_title is not None or artist is not None
                else "sidecar" if sidecar_identity else "filename"
            ),
            "songTitle": resolved_title,
            "referenceArtist": resolved_artist,
        },
        "insertBody": {
            "snippet": {
                "title": title,
                "description": description,
                "tags": tags,
                "categoryId": str(rules.get("categoryId", "10")),
                "defaultLanguage": str(rules.get("defaultLanguage", "vi")),
                "defaultAudioLanguage": str(
                    rules.get("defaultAudioLanguage", rules.get("defaultLanguage", "vi"))
                ),
            },
            "status": {
                "privacyStatus": privacy,
                "license": str(channel.get("license", "youtube")),
                "embeddable": bool(channel.get("embeddable", True)),
                "publicStatsViewable": bool(
                    channel.get("publicStatsViewable", True)
                ),
                "selfDeclaredMadeForKids": bool(channel.get("madeForKids", False)),
                "containsSyntheticMedia": bool(
                    channel.get("containsSyntheticMedia", False)
                ),
            },
        },
        "uploadOptions": {
            "notifySubscribers": bool(channel.get("notifySubscribers", False)),
            "resumable": True,
        },
        "postUpload": {
            "playlistId": str(channel.get("defaultPlaylistId", "")).strip(),
            "thumbnail": "thumbnail.jpg",
            "publishDelayHours": int(channel.get("publishDelayHours", 24)),
        },
        "warnings": warnings,
    }
