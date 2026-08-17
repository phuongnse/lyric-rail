from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable


YOUTUBE_SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.force-ssl",
]


def build_youtube_service(client_secret: Path, token: Path) -> Any:
    """Authorize a desktop OAuth client and return a YouTube Data API service."""
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
    except ImportError as exc:
        raise RuntimeError(
            "YouTube dependencies are missing. Install LyricRail with the 'youtube' extra."
        ) from exc

    if not client_secret.is_file():
        raise ValueError(f"YouTube OAuth client secret does not exist: {client_secret}")
    credentials = None
    if token.is_file():
        credentials = Credentials.from_authorized_user_file(str(token), YOUTUBE_SCOPES)
    if credentials and credentials.expired and credentials.refresh_token:
        credentials.refresh(Request())
    if not credentials or not credentials.valid:
        flow = InstalledAppFlow.from_client_secrets_file(
            str(client_secret), scopes=YOUTUBE_SCOPES
        )
        credentials = flow.run_local_server(port=0, open_browser=True)
    token.parent.mkdir(parents=True, exist_ok=True)
    token.write_text(credentials.to_json(), encoding="utf-8")
    return build("youtube", "v3", credentials=credentials, cache_discovery=False)


def verify_channel(service: Any, expected_channel_id: str) -> dict[str, str]:
    response = service.channels().list(part="id,snippet", mine=True).execute()
    items = list(response.get("items", []))
    if len(items) != 1:
        raise RuntimeError("OAuth account did not resolve to exactly one YouTube channel.")
    channel_id = str(items[0].get("id", "")).strip()
    title = str(items[0].get("snippet", {}).get("title", "")).strip()
    if expected_channel_id and channel_id != expected_channel_id:
        raise RuntimeError(
            f"OAuth is connected to channel {channel_id} ({title}), not the configured "
            f"channel {expected_channel_id}."
        )
    return {"channelId": channel_id, "channelTitle": title}


def execute_resumable(
    request: Any,
    *,
    progress: Callable[[float], None] | None = None,
    retries: int = 5,
) -> dict[str, Any]:
    response = None
    failures = 0
    while response is None:
        try:
            status, response = request.next_chunk()
            if status is not None and progress:
                progress(float(status.progress()) * 100.0)
            failures = 0
        except Exception as exc:  # google's HttpError is an optional dependency
            http_status = getattr(getattr(exc, "resp", None), "status", None)
            if http_status not in {500, 502, 503, 504} or failures >= retries:
                raise
            failures += 1
            time.sleep(min(2**failures, 30))
    if not isinstance(response, dict):
        raise RuntimeError("YouTube returned an invalid resumable-upload response.")
    return response


def upload_video(
    service: Any,
    video: Path,
    body: dict[str, Any],
    *,
    notify_subscribers: bool,
    progress: Callable[[float], None] | None = None,
) -> dict[str, Any]:
    from googleapiclient.http import MediaFileUpload

    media = MediaFileUpload(
        str(video), mimetype="video/mp4", chunksize=8 * 1024 * 1024, resumable=True
    )
    request = service.videos().insert(
        part="snippet,status",
        body=body,
        media_body=media,
        notifySubscribers=notify_subscribers,
    )
    response = execute_resumable(request, progress=progress)
    if not str(response.get("id", "")).strip():
        raise RuntimeError("YouTube upload completed without returning a video ID.")
    return response


def set_thumbnail(service: Any, video_id: str, thumbnail: Path) -> dict[str, Any]:
    from googleapiclient.http import MediaFileUpload

    return service.thumbnails().set(
        videoId=video_id,
        media_body=MediaFileUpload(str(thumbnail), mimetype="image/jpeg", resumable=False),
    ).execute()


def add_to_playlist(service: Any, video_id: str, playlist_id: str) -> dict[str, Any]:
    return service.playlistItems().insert(
        part="snippet",
        body={
            "snippet": {
                "playlistId": playlist_id,
                "resourceId": {"kind": "youtube#video", "videoId": video_id},
            }
        },
    ).execute()


def processing_status(service: Any, video_id: str) -> dict[str, Any]:
    response = service.videos().list(part="processingDetails,status", id=video_id).execute()
    items = list(response.get("items", []))
    if not items:
        raise RuntimeError(f"YouTube video disappeared while processing: {video_id}")
    return items[0]


def publish_video(
    service: Any,
    video_id: str,
    *,
    privacy: str,
    delay_hours: int = 0,
) -> dict[str, Any]:
    response = service.videos().list(part="status", id=video_id).execute()
    items = list(response.get("items", []))
    if not items:
        raise RuntimeError(f"Cannot publish missing YouTube video: {video_id}")
    status = dict(items[0].get("status", {}))
    if privacy == "public" and delay_hours > 0:
        status["privacyStatus"] = "private"
        status["publishAt"] = (
            datetime.now(timezone.utc) + timedelta(hours=delay_hours)
        ).isoformat(timespec="seconds").replace("+00:00", "Z")
    else:
        status["privacyStatus"] = privacy
        status.pop("publishAt", None)
    return service.videos().update(
        part="status", body={"id": video_id, "status": status}
    ).execute()


def write_receipt(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
