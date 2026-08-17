from __future__ import annotations

from pathlib import Path
from typing import Any


def build_release_metadata(
    job: dict[str, Any],
    delivery_metadata: dict[str, Any],
    source_directives: dict[str, Any],
    pipeline: dict[str, Any],
) -> dict[str, Any]:
    """Build encrypted, portable metadata without leaking local filesystem paths."""
    request = job.get("request", {})
    source_identity = delivery_metadata.get("source", {})
    snippet = delivery_metadata.get("insertBody", {}).get("snippet", {})
    source_record = source_directives.get("source", {})
    sources: list[dict[str, Any]] = []
    if request.get("sourceOrigin") == "url":
        input_url = str(source_record.get("inputUrl") or request.get("sourceInput") or "")
        webpage_url = str(source_record.get("webpageUrl") or input_url)
        sources.append(
            {
                "kind": "online-media",
                "url": input_url,
                "webpageUrl": webpage_url,
                "provider": str(source_record.get("extractor") or ""),
                "providerId": str(source_record.get("id") or ""),
                "range": source_record.get("range") or request.get("sourceRange") or {},
            }
        )
    else:
        media = Path(str(request.get("sourceMedia") or request.get("sourceVideo") or "source"))
        sources.append(
            {
                "kind": "local-media",
                "fileName": media.name,
                "range": request.get("sourceRange") or {},
            }
        )

    app_playback = pipeline.get("quality", {}).get("appPlayback", {})
    return {
        "schemaVersion": 1,
        "jobId": str(job.get("jobId") or ""),
        "title": str(source_identity.get("songTitle") or "").strip(),
        "referenceArtist": str(source_identity.get("referenceArtist") or "").strip(),
        "description": str(snippet.get("description") or "").strip(),
        "tags": list(snippet.get("tags") or []),
        "language": str(snippet.get("defaultLanguage") or "vi"),
        "credits": [
            {
                "role": "reference-performance",
                "name": str(source_identity.get("referenceArtist") or "").strip(),
            }
        ],
        "sources": sources,
        "rights": {
            "ownershipClaimed": False,
            "licenseProvided": False,
            "intendedUse": "private-personal",
            "notice": (
                "Attribution is informational only. This package does not grant "
                "copyright, music, performance, or video rights."
            ),
        },
        "lyrics": {
            "mode": "authoritative-input",
            "sha256": str(request.get("lyrics", {}).get("sha256") or ""),
            "lineCount": int(request.get("lyrics", {}).get("lineCount") or 0),
            "wordCount": int(request.get("lyrics", {}).get("wordCount") or 0),
            "dynamicRendering": True,
        },
        "playback": {
            "layout": "synchronized-encrypted-assets",
            "videoContainer": str(app_playback.get("videoContainer") or "mp4"),
            "karaokeAudioContainer": str(
                app_playback.get("audioContainer") or "m4a"
            ),
            "originalAudioPolicy": "bitstream-copy-aac-mp3-full-timeline-else-aac",
            "videoPolicy": str(
                app_playback.get("videoPolicy") or "stream-copy-when-timeline-allows"
            ),
            "audioTracks": [
                {
                    "index": 0,
                    "id": "karaoke",
                    "name": "Karaoke",
                    "language": "vi",
                    "default": True,
                },
                {
                    "index": 1,
                    "id": "original-reference",
                    "name": "Original Reference",
                    "language": "vi",
                    "default": False,
                },
            ],
        },
        "producer": {
            "name": "LyricRail Studio",
            "version": str(job.get("runtime", {}).get("lyricRailVersion") or ""),
        },
    }


def build_package_request(
    release_metadata: dict[str, Any],
    *,
    playback_video: Path,
    karaoke_audio: Path,
    original_audio: Path,
    lyrics_timing: Path,
    render_plan: Path,
    release_metadata_file: Path,
    presentation_template: Path,
    minimum_player_version: str,
    visual_license_manifest: Path | None = None,
) -> dict[str, Any]:
    original_delivery = {
        ".m4a": ("audio/mp4", "m4a"),
        ".mp3": ("audio/mpeg", "mp3"),
    }.get(original_audio.suffix.lower())
    if original_delivery is None:
        raise ValueError(
            "Original Reference must use a portable .m4a or .mp3 delivery container"
        )
    original_media_type, original_extension = original_delivery
    assets: list[dict[str, Any]] = [
        {
            "logicalName": "media/video.mp4",
            "path": str(playback_video.resolve()),
            "mediaType": "video/mp4",
            "kind": "playback-video",
            "default": True,
        },
        {
            "logicalName": "audio/karaoke.m4a",
            "path": str(karaoke_audio.resolve()),
            "mediaType": "audio/mp4",
            "kind": "playback-audio",
            "language": "vi",
            "default": True,
        },
        {
            "logicalName": f"audio/original-reference.{original_extension}",
            "path": str(original_audio.resolve()),
            "mediaType": original_media_type,
            "kind": "playback-audio",
            "language": "vi",
            "default": False,
        },
        {
            "logicalName": "lyrics/timing.json",
            "path": str(lyrics_timing.resolve()),
            "mediaType": "application/json",
            "kind": "lyrics-timing",
            "language": "vi",
        },
        {
            "logicalName": "lyrics/render-plan.json",
            "path": str(render_plan.resolve()),
            "mediaType": "application/json",
            "kind": "lyrics-render-plan",
            "language": "vi",
        },
        {
            "logicalName": "metadata/release.json",
            "path": str(release_metadata_file.resolve()),
            "mediaType": "application/json",
            "kind": "release-metadata",
        },
        {
            "logicalName": "presentation/template.json",
            "path": str(presentation_template.resolve()),
            "mediaType": "application/json",
            "kind": "presentation-template",
        },
    ]
    if visual_license_manifest and visual_license_manifest.is_file():
        assets.append(
            {
                "logicalName": "sources/visual-license.json",
                "path": str(visual_license_manifest.resolve()),
                "mediaType": "application/json",
                "kind": "source-attribution",
            }
        )
    return {
        "metadata": release_metadata,
        "assets": assets,
        "producer": str(release_metadata.get("producer", {}).get("name") or "LyricRail"),
        "minimumPlayerVersion": minimum_player_version,
    }
