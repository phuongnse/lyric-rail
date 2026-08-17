from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path
from typing import Any

from .config import load_project_config, resolve_environment_path
from .job import atomic_write_json
from .runner import StageContext, StageHandler
from .youtube import (
    add_to_playlist,
    build_youtube_service,
    processing_status,
    publish_video,
    set_thumbnail,
    upload_video,
    verify_channel,
)


def _job(context: StageContext) -> dict[str, Any]:
    return context.store.load(context.job_id)


def _root(context: StageContext) -> Path:
    return context.job_directory.parent.parent


def _metadata(context: StageContext) -> dict[str, Any]:
    return json.loads((context.job_directory / "metadata.json").read_text(encoding="utf-8"))


def _artifact_path(job: dict[str, Any], kind: str) -> Path:
    matches = [Path(item["path"]) for item in job.get("artifacts", []) if item.get("kind") == kind]
    if not matches or not matches[-1].is_file():
        raise ValueError(f"Required artifact is missing: {kind}")
    return matches[-1]


def _youtube_receipt(context: StageContext) -> Path:
    return context.artifacts_directory / "youtube.json"


def _read_receipt(context: StageContext) -> dict[str, Any]:
    path = _youtube_receipt(context)
    if not path.is_file():
        raise ValueError("YouTube upload receipt is missing.")
    return json.loads(path.read_text(encoding="utf-8"))


def _video_id(context: StageContext) -> str:
    value = str(_read_receipt(context).get("videoId", "")).strip()
    if not value:
        raise ValueError("YouTube upload receipt has no video ID.")
    return value


def _json_artifact(path: Path, kind: str, label: str, **values: Any) -> dict[str, Any]:
    return {
        "kind": kind,
        "label": label,
        "path": str(path.resolve()),
        "sizeBytes": path.stat().st_size,
        **values,
    }


def _draw_thumbnail(frame: Path, output: Path, metadata: dict[str, Any], root: Path) -> None:
    try:
        from PIL import Image, ImageDraw, ImageFont, ImageOps
    except ImportError as exc:
        raise RuntimeError("Thumbnail creation requires Pillow.") from exc

    image = ImageOps.fit(Image.open(frame).convert("RGB"), (1280, 720))
    canvas = image.convert("RGBA")
    shade = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    shade_draw = ImageDraw.Draw(shade)
    for y in range(220, 720):
        alpha = round(210 * (y - 220) / 500)
        shade_draw.line((0, y, 1280, y), fill=(0, 0, 0, alpha))
    canvas = Image.alpha_composite(canvas, shade)
    draw = ImageDraw.Draw(canvas)
    font_directory = root / "assets" / "fonts"
    bold = font_directory / "BeVietnamPro-ExtraBold.ttf"
    medium = font_directory / "BeVietnamPro-Medium.ttf"
    source = metadata.get("source", {})
    title = str(source.get("songTitle", "Karaoke")).strip()
    artist = str(source.get("referenceArtist", "")).strip()

    label_font = ImageFont.truetype(str(bold), 34)
    draw.rounded_rectangle((70, 350, 310, 410), radius=18, fill=(219, 32, 63, 235))
    draw.text((91, 357), "KARAOKE", font=label_font, fill="white")

    title_size = 74
    while title_size > 38:
        title_font = ImageFont.truetype(str(bold), title_size)
        if draw.textbbox((0, 0), title, font=title_font)[2] <= 1140:
            break
        title_size -= 2
    title_font = ImageFont.truetype(str(bold), title_size)
    draw.text(
        (70, 430), title, font=title_font, fill="white",
        stroke_width=2, stroke_fill=(0, 0, 0, 180),
    )
    if artist:
        artist_font = ImageFont.truetype(str(medium), 38)
        draw.text((73, 545), artist, font=artist_font, fill=(235, 235, 235, 255))
    canvas.convert("RGB").save(output, format="JPEG", quality=90, optimize=True)


def build_youtube_handlers(root: Path) -> dict[str, StageHandler]:
    service_cache: dict[str, Any] = {}

    def service(context: StageContext) -> Any:
        if "service" not in service_cache:
            config = load_project_config(root)["channel"]
            client_secret = resolve_environment_path(
                "YOUTUBE_CLIENT_SECRET_PATH", root, "credentials/client_secret.json"
            )
            token = resolve_environment_path(
                "YOUTUBE_TOKEN_PATH", root, "credentials/token.json"
            )
            context.progress(2, "Authorizing YouTube channel")
            api = build_youtube_service(client_secret, token)
            identity = verify_channel(api, str(config.get("expectedChannelId", "")).strip())
            context.log(
                f"Verified YouTube channel {identity['channelTitle']} ({identity['channelId']})"
            )
            service_cache["service"] = api
        return service_cache["service"]

    def create_thumbnail(context: StageContext) -> list[dict[str, Any]]:
        job = _job(context)
        video = _artifact_path(job, "video-youtube-upload")
        thumbnail = context.artifacts_directory / "thumbnail.jpg"
        frame = context.work_directory / "thumbnail-frame.jpg"
        ffmpeg = resolve_environment_path("LYRICRAIL_FFMPEG", root, "ffmpeg")
        command = [
            str(ffmpeg), "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
            "-ss", "2", "-i", str(video), "-frames:v", "1",
            "-vf", "scale=1280:720:force_original_aspect_ratio=increase,crop=1280:720",
            "-q:v", "2", str(frame),
        ]
        context.log("Command: " + subprocess.list2cmdline(command))
        completed = subprocess.run(command, capture_output=True, text=True, check=False)
        if completed.returncode or not frame.is_file():
            raise RuntimeError("Unable to create YouTube thumbnail: " + completed.stderr[-2000:])
        _draw_thumbnail(frame, thumbnail, _metadata(context), root)
        frame.unlink(missing_ok=True)
        if thumbnail.stat().st_size > 2 * 1024 * 1024:
            raise RuntimeError("Generated YouTube thumbnail exceeds the 2 MiB API limit.")
        context.progress(100, "Created 1280x720 YouTube thumbnail")
        return [_json_artifact(thumbnail, "thumbnail", "YouTube thumbnail")]

    def upload(context: StageContext) -> list[dict[str, Any]]:
        job = _job(context)
        metadata = _metadata(context)
        video = _artifact_path(job, "video-youtube-upload")
        response = upload_video(
            service(context),
            video,
            metadata["insertBody"],
            notify_subscribers=bool(metadata.get("uploadOptions", {}).get("notifySubscribers", False)),
            progress=lambda percent: context.progress(max(3.0, percent), "Uploading to YouTube"),
        )
        video_id = str(response["id"])
        thumbnail_items = [
            Path(item["path"]) for item in _job(context).get("artifacts", [])
            if item.get("kind") == "thumbnail"
        ]
        if thumbnail_items and thumbnail_items[-1].is_file():
            set_thumbnail(service(context), video_id, thumbnail_items[-1])
        receipt = {
            "schemaVersion": 1,
            "videoId": video_id,
            "url": f"https://youtu.be/{video_id}",
            "uploadResponse": response,
        }
        atomic_write_json(_youtube_receipt(context), receipt)
        context.progress(100, f"Uploaded YouTube video {video_id}")
        return [
            _json_artifact(
                _youtube_receipt(context), "youtube-video", "YouTube video receipt",
                videoId=video_id, url=receipt["url"],
            )
        ]

    def playlist(context: StageContext) -> list[dict[str, Any]]:
        playlist_id = str(_metadata(context)["postUpload"].get("playlistId", "")).strip()
        if not playlist_id:
            context.progress(100, "No default playlist configured; nothing to attach")
            return []
        response = add_to_playlist(service(context), _video_id(context), playlist_id)
        path = context.artifacts_directory / "youtube-playlist.json"
        atomic_write_json(path, response)
        context.progress(100, "Added video to the configured playlist")
        return [_json_artifact(path, "youtube-playlist", "YouTube playlist receipt")]

    def wait_processing(context: StageContext) -> list[dict[str, Any]]:
        deadline = time.monotonic() + 60 * 60
        while True:
            context.checkpoint()
            response = processing_status(service(context), _video_id(context))
            details = response.get("processingDetails", {})
            status = str(details.get("processingStatus", "")).casefold()
            if status == "succeeded":
                path = context.artifacts_directory / "youtube-processing.json"
                atomic_write_json(path, response)
                context.progress(100, "YouTube processing completed")
                return [_json_artifact(path, "youtube-processing", "YouTube processing status")]
            if status in {"failed", "terminated"}:
                raise RuntimeError(f"YouTube processing ended with status: {status}")
            if time.monotonic() >= deadline:
                raise TimeoutError("YouTube processing did not finish within 60 minutes.")
            context.progress(50, f"YouTube processing status: {status or 'pending'}")
            time.sleep(15)

    def publish(context: StageContext) -> list[dict[str, Any]]:
        channel = load_project_config(root)["channel"]
        response = publish_video(
            service(context), _video_id(context),
            privacy=str(channel.get("defaultPrivacy", "private")),
            delay_hours=int(channel.get("publishDelayHours", 0)),
        )
        path = context.artifacts_directory / "youtube-publish.json"
        atomic_write_json(path, response)
        context.progress(100, "Applied YouTube publishing policy")
        return [_json_artifact(path, "youtube-publish", "YouTube publishing receipt")]

    return {
        "create_thumbnail": create_thumbnail,
        "upload_youtube": upload,
        "attach_playlist": playlist,
        "wait_processing": wait_processing,
        "publish": publish,
    }
