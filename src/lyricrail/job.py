from __future__ import annotations

import json
import os
import platform
import re
import socket
import tempfile
import time
import unicodedata
import uuid
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

from . import __version__


JOB_SCHEMA_VERSION = 1
TERMINAL_JOB_STATUSES = {"succeeded", "failed", "blocked", "cancelled"}
ACTIVE_JOB_STATUSES = {"queued", "running", "cancelling"}
VALID_JOB_STATUSES = ACTIVE_JOB_STATUSES | TERMINAL_JOB_STATUSES | {
    "planned",
    "blocked",
}
VALID_STAGE_STATUSES = {
    "pending",
    "running",
    "succeeded",
    "failed",
    "blocked",
    "cancelled",
    "skipped",
}
JOB_TRANSITIONS = {
    "planned": {"queued", "cancelled"},
    "queued": {"running", "cancelling", "cancelled"},
    "running": {"succeeded", "failed", "blocked", "cancelling", "cancelled"},
    "cancelling": {"cancelled", "failed"},
    "blocked": {"queued", "running", "cancelled"},
    "failed": {"queued"},
    "succeeded": set(),
    "cancelled": {"queued"},
}
STAGE_TRANSITIONS = {
    "pending": {"running", "blocked", "cancelled", "skipped"},
    "running": {"succeeded", "failed", "blocked", "cancelled"},
    "blocked": {"running", "cancelled"},
    "failed": {"running", "cancelled"},
    "cancelled": {"pending", "running"},
    "succeeded": set(),
    "skipped": set(),
}


@dataclass(frozen=True)
class StageSpec:
    key: str
    title: str
    weight: float
    group: str


STAGE_SPECS = (
    StageSpec("probe", "Analyze source media", 3.0, "media"),
    StageSpec("extract_audio", "Extract lossless audio", 4.0, "media"),
    StageSpec("separate_stems", "Separate instrumental and vocals", 20.0, "audio"),
    StageSpec("load_lyrics", "Load authoritative lyrics", 2.0, "lyrics"),
    StageSpec("align_lyrics", "Align authoritative lyrics to audio", 25.0, "lyrics"),
    StageSpec("classify_roles", "Classify vocal roles", 8.0, "lyrics"),
    StageSpec("prepare_visuals", "Prepare song-specific landscape", 12.0, "media"),
    StageSpec("render_subtitles", "Build karaoke subtitles", 8.0, "render"),
    StageSpec("render_player_media", "Build compact synchronized playback assets", 12.0, "package"),
    StageSpec("package_lrail", "Build authenticated LyricRail package", 3.0, "package"),
    StageSpec("cleanup_intermediates", "Remove verified cleartext intermediates", 1.0, "package"),
    StageSpec("render_master", "Render the legacy YouTube upload MP4", 23.0, "youtube"),
    StageSpec("create_thumbnail", "Create thumbnail", 2.0, "youtube"),
    StageSpec("upload_youtube", "Upload YouTube resumable", 3.0, "youtube"),
    StageSpec("attach_playlist", "Add video to playlist", 0.5, "youtube"),
    StageSpec("wait_processing", "Wait for YouTube processing", 0.5, "youtube"),
    StageSpec("publish", "Publish or schedule video", 0.5, "youtube"),
)
STAGE_BY_KEY = {stage.key: stage for stage in STAGE_SPECS}


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


def slugify(value: str) -> str:
    value = value.replace("Đ", "D").replace("đ", "d")
    value = unicodedata.normalize("NFKD", value)
    value = "".join(char for char in value if not unicodedata.combining(char))
    value = re.sub(r"[^A-Za-z0-9]+", "-", value).strip("-").lower()
    return value or "karaoke"


def atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"File not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON at {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"Data must be a JSON object: {path}")
    return data


def append_json_line(path: Path, event: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = (json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n").encode(
        "utf-8"
    )
    descriptor = os.open(path, os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600)
    try:
        os.write(descriptor, line)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def stage_is_enabled(key: str, pipeline: dict[str, Any], upload: bool) -> tuple[bool, str]:
    youtube = pipeline.get("youtube", {})
    if STAGE_BY_KEY[key].group == "package":
        package = pipeline.get("package", {})
        if not bool(package.get("enabled", True)):
            return False, "package.enabled=false"
        if key == "cleanup_intermediates" and not bool(
            package.get("cleanupVerifiedIntermediates", False)
        ):
            return False, "package.cleanupVerifiedIntermediates=false"
        return True, ""
    if STAGE_BY_KEY[key].group != "youtube":
        return True, ""
    if not upload:
        return False, "YouTube delivery disabled for this job"
    option_by_stage = {
        "create_thumbnail": "uploadThumbnail",
        "attach_playlist": "addToPlaylist",
        "wait_processing": "waitForProcessing",
        "publish": "publishWhenReady",
    }
    option = option_by_stage.get(key)
    if option and not bool(youtube.get(option, False)):
        return False, f"youtube.{option}=false"
    return True, ""


def build_stage_plan(pipeline: dict[str, Any], upload: bool) -> list[dict[str, Any]]:
    stages: list[dict[str, Any]] = []
    for index, spec in enumerate(STAGE_SPECS, start=1):
        enabled, reason = stage_is_enabled(spec.key, pipeline, upload)
        stages.append(
            {
                "index": index,
                "key": spec.key,
                "title": spec.title,
                "group": spec.group,
                "weight": spec.weight,
                "status": "pending" if enabled else "skipped",
                "progressPercent": 0.0 if enabled else 100.0,
                "attempts": 0,
                "startedAt": None,
                "finishedAt": None,
                "durationSeconds": None,
                "skipReason": reason or None,
                "error": None,
                "logFile": f"logs/{spec.key}.log",
            }
        )
    return stages


def calculate_progress(stages: list[dict[str, Any]]) -> float:
    active = [stage for stage in stages if stage["status"] != "skipped"]
    total_weight = sum(float(stage["weight"]) for stage in active)
    if total_weight <= 0:
        return 100.0
    earned = 0.0
    for stage in active:
        status = stage["status"]
        if status == "succeeded":
            fraction = 1.0
        elif status == "running":
            fraction = max(0.0, min(100.0, float(stage["progressPercent"]))) / 100.0
        else:
            fraction = 0.0
        earned += float(stage["weight"]) * fraction
    return round(earned / total_weight * 100.0, 2)


def build_plan(
    video: Path,
    pipeline: dict[str, Any],
    metadata: dict[str, Any],
    upload: bool,
) -> dict[str, Any]:
    stages = build_stage_plan(pipeline, upload)
    return {
        "schemaVersion": JOB_SCHEMA_VERSION,
        "kind": "lyricrail.plan",
        "sourceVideo": str(video.resolve()),
        "qualityMode": pipeline.get("quality", {}).get("mode", "maximum"),
        "uploadEnabled": upload,
        "youtubePrivacy": metadata.get("insertBody", {})
        .get("status", {})
        .get("privacyStatus", "private"),
        "metadataPreview": {
            "title": metadata.get("insertBody", {}).get("snippet", {}).get("title", ""),
            "categoryId": metadata.get("insertBody", {})
            .get("snippet", {})
            .get("categoryId", ""),
            "tags": metadata.get("insertBody", {}).get("snippet", {}).get("tags", []),
        },
        "stages": stages,
        "warnings": list(metadata.get("warnings", [])),
    }


def _duration_seconds(started: str | None, finished: str | None) -> float | None:
    if not started or not finished:
        return None
    return round(
        (datetime.fromisoformat(finished) - datetime.fromisoformat(started)).total_seconds(),
        3,
    )


class JobStore:
    def __init__(self, output_root: Path):
        self.output_root = output_root.resolve()
        self.output_root.mkdir(parents=True, exist_ok=True)

    def _job_directory(self, job_id: str) -> Path:
        if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._-]{2,180}", job_id):
            raise ValueError(f"Invalid job ID: {job_id}")
        return self.output_root / job_id

    def resolve_job_id(self, reference: str) -> str:
        if reference != "latest":
            return reference
        jobs = self.list_jobs(limit=1)
        if not jobs:
            raise ValueError("No jobs found.")
        return str(jobs[0]["jobId"])

    def create(
        self,
        video: Path,
        pipeline: dict[str, Any],
        metadata: dict[str, Any],
        upload: bool,
        *,
        source_input: str | None = None,
        source_origin: str = "local",
        source_kind_hint: str = "video",
        requested_start_seconds: float | None = None,
        requested_end_seconds: float | None = None,
        media_trim_start_seconds: float | None = None,
        media_trim_end_seconds: float | None = None,
        source_pretrimmed: bool = False,
        lyrics_text: str,
        lyrics_source_path: Path,
        lyrics_sha256: str,
        lyrics_line_count: int,
        lyrics_word_count: int,
        project_root: Path | None = None,
    ) -> dict[str, Any]:
        if not lyrics_text.strip():
            raise ValueError("Authoritative lyrics must not be empty.")
        timestamp = datetime.now().astimezone()
        suffix = uuid.uuid4().hex[:6]
        job_id = f"{timestamp:%Y%m%d-%H%M%S}-{slugify(video.stem)[:80]}-{suffix}"
        job_directory = self._job_directory(job_id)
        job_directory.mkdir(parents=True, exist_ok=False)
        for child in ("artifacts", "inputs", "logs", "work"):
            (job_directory / child).mkdir()
        lyric_snapshot = job_directory / "inputs" / "lyrics.txt"
        lyric_snapshot.write_text(lyrics_text, encoding="utf-8", newline="\n")

        stages = build_stage_plan(pipeline, upload)
        manifest: dict[str, Any] = {
            "schemaVersion": JOB_SCHEMA_VERSION,
            "kind": "lyricrail.job",
            "jobId": job_id,
            "status": "queued",
            "currentStage": None,
            "progressPercent": calculate_progress(stages),
            "createdAt": timestamp.isoformat(timespec="milliseconds"),
            "startedAt": None,
            "finishedAt": None,
            "updatedAt": timestamp.isoformat(timespec="milliseconds"),
            "durationSeconds": None,
            "request": {
                "sourceInput": source_input or str(video.resolve()),
                "sourceOrigin": source_origin,
                "sourceKindHint": source_kind_hint,
                "sourceMedia": str(video.resolve()),
                "sourceVideo": str(video.resolve()),
                "sourceRange": {
                    "startSeconds": requested_start_seconds,
                    "endSeconds": requested_end_seconds,
                },
                "mediaTrim": {
                    "startSeconds": media_trim_start_seconds,
                    "endSeconds": media_trim_end_seconds,
                },
                "sourcePretrimmed": source_pretrimmed,
                "lyrics": {
                    "mode": "authoritative-input",
                    "sourcePath": str(lyrics_source_path.resolve()),
                    "snapshot": "inputs/lyrics.txt",
                    "sha256": lyrics_sha256,
                    "lineCount": lyrics_line_count,
                    "wordCount": lyrics_word_count,
                    "detectedTextUsed": False,
                    "captionUsed": False,
                },
                "uploadEnabled": upload,
                "qualityMode": pipeline.get("quality", {}).get("mode", "maximum"),
            },
            "runtime": {
                "lyricRailVersion": __version__,
                "projectRoot": str((project_root or self.output_root.parent).resolve()),
                "host": socket.gethostname(),
                "platform": platform.platform(),
                "createdByPid": os.getpid(),
            },
            "execution": {
                "isolationPolicy": "fresh-job-no-intermediate-reuse",
                "crossJobIntermediateReuse": False,
                "sharedCaches": ["source-media", "model-weights"],
            },
            "paths": {
                "jobDirectory": str(job_directory),
                "metadata": "metadata.json",
                "events": "events.jsonl",
                "pipelineLog": "logs/pipeline.log",
                "artifacts": "artifacts",
                "inputs": "inputs",
                "work": "work",
            },
            "stages": stages,
            "artifacts": [],
            "warnings": list(metadata.get("warnings", [])),
            "error": None,
            "retryCount": 0,
        }
        atomic_write_json(job_directory / "metadata.json", metadata)
        atomic_write_json(job_directory / "job.json", manifest)
        self._append_event(job_id, "job.created", {"status": "queued"})
        self.log(job_id, "Job created", level="INFO")
        return manifest

    def load(self, reference: str) -> dict[str, Any]:
        job_id = self.resolve_job_id(reference)
        manifest = read_json(self._job_directory(job_id) / "job.json")
        self._validate_manifest(manifest)
        return manifest

    def list_jobs(
        self, limit: int = 20, statuses: set[str] | None = None
    ) -> list[dict[str, Any]]:
        jobs: list[dict[str, Any]] = []
        if not self.output_root.exists():
            return jobs
        candidates = [path for path in self.output_root.iterdir() if path.is_dir()]
        for directory in candidates:
            manifest_path = directory / "job.json"
            if not manifest_path.is_file():
                continue
            try:
                manifest = read_json(manifest_path)
                self._validate_manifest(manifest)
            except ValueError:
                continue
            if statuses and manifest["status"] not in statuses:
                continue
            jobs.append(manifest)
        jobs.sort(key=lambda item: item.get("createdAt", ""), reverse=True)
        return jobs[:limit]

    @contextmanager
    def _lock(self, job_id: str, timeout: float = 5.0) -> Iterator[None]:
        lock_path = self._job_directory(job_id) / ".job.lock"
        deadline = time.monotonic() + timeout
        while True:
            try:
                descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                payload = json.dumps(
                    {"pid": os.getpid(), "host": socket.gethostname(), "createdAt": now_iso()}
                ).encode("utf-8")
                os.write(descriptor, payload)
                os.close(descriptor)
                break
            except FileExistsError:
                try:
                    if time.time() - lock_path.stat().st_mtime > 300:
                        lock_path.unlink()
                        continue
                except FileNotFoundError:
                    continue
                if time.monotonic() >= deadline:
                    raise ValueError(f"Job is being updated by another process: {job_id}")
                time.sleep(0.05)
        try:
            yield
        finally:
            try:
                lock_path.unlink()
            except FileNotFoundError:
                pass

    def update_job(self, reference: str, **changes: Any) -> dict[str, Any]:
        job_id = self.resolve_job_id(reference)
        with self._lock(job_id):
            manifest = self.load(job_id)
            requested_status = changes.get("status")
            if requested_status is not None and requested_status != manifest["status"]:
                allowed = JOB_TRANSITIONS.get(manifest["status"], set())
                if requested_status not in allowed:
                    raise ValueError(
                        f"Invalid job transition: {manifest['status']} -> {requested_status}"
                    )
            for key, value in changes.items():
                if key not in manifest:
                    raise ValueError(f"Cannot update unknown field: {key}")
                manifest[key] = value
            manifest["updatedAt"] = now_iso()
            manifest["progressPercent"] = calculate_progress(manifest["stages"])
            manifest["durationSeconds"] = _duration_seconds(
                manifest.get("startedAt"), manifest.get("finishedAt")
            )
            self._validate_manifest(manifest)
            atomic_write_json(self._job_directory(job_id) / "job.json", manifest)
        self._append_event(job_id, "job.updated", changes)
        return manifest

    def update_stage(
        self,
        reference: str,
        stage_key: str,
        status: str | None = None,
        progress_percent: float | None = None,
        error: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        job_id = self.resolve_job_id(reference)
        if stage_key not in STAGE_BY_KEY:
            raise ValueError(f"Stage does not exist: {stage_key}")
        if status is not None and status not in VALID_STAGE_STATUSES:
            raise ValueError(f"Invalid stage status: {status}")

        with self._lock(job_id):
            manifest = self.load(job_id)
            stage = next(item for item in manifest["stages"] if item["key"] == stage_key)
            if status is not None and status != stage["status"]:
                allowed = STAGE_TRANSITIONS.get(stage["status"], set())
                if status not in allowed:
                    raise ValueError(
                        f"Invalid stage transition: {stage['status']} -> {status}"
                    )
            if status == "running" and stage["status"] != "running":
                stage["attempts"] += 1
                stage["startedAt"] = now_iso()
                stage["finishedAt"] = None
                stage["error"] = None
            if progress_percent is not None:
                stage["progressPercent"] = round(
                    max(0.0, min(100.0, float(progress_percent))), 2
                )
            if status is not None:
                stage["status"] = status
                if status == "succeeded":
                    stage["progressPercent"] = 100.0
                if status in {"succeeded", "failed", "blocked", "cancelled"}:
                    stage["finishedAt"] = now_iso()
                    stage["durationSeconds"] = _duration_seconds(
                        stage.get("startedAt"), stage.get("finishedAt")
                    )
            if error is not None:
                stage["error"] = deepcopy(error)
            manifest["currentStage"] = stage_key if stage["status"] == "running" else None
            manifest["updatedAt"] = now_iso()
            manifest["progressPercent"] = calculate_progress(manifest["stages"])
            self._validate_manifest(manifest)
            atomic_write_json(self._job_directory(job_id) / "job.json", manifest)

        self._append_event(
            job_id,
            "stage.updated",
            {
                "stage": stage_key,
                "status": stage["status"],
                "progressPercent": stage["progressPercent"],
                "error": stage["error"],
            },
        )
        return manifest

    def request_cancel(self, reference: str) -> dict[str, Any]:
        job = self.load(reference)
        if job["status"] not in ACTIVE_JOB_STATUSES:
            raise ValueError(f"Cannot cancel a job in status {job['status']}")
        marker = self._job_directory(job["jobId"]) / "cancel.requested.json"
        atomic_write_json(marker, {"requestedAt": now_iso(), "requestedByPid": os.getpid()})
        if job["status"] == "queued":
            return self.update_job(
                job["jobId"],
                status="cancelled",
                finishedAt=now_iso(),
                error={
                    "code": "JOB_CANCELLED",
                    "message": "The job was cancelled before it started.",
                    "retryable": True,
                },
            )
        return self.update_job(job["jobId"], status="cancelling")

    def prepare_retry(
        self, reference: str, from_stage: str | None = None
    ) -> dict[str, Any]:
        job_id = self.resolve_job_id(reference)
        with self._lock(job_id):
            manifest = self.load(job_id)
            retryable_terminal = manifest["status"] in {
                "failed",
                "blocked",
                "cancelled",
            }
            explicit_reprocess = manifest["status"] == "succeeded" and bool(from_stage)
            if not retryable_terminal and not explicit_reprocess:
                raise ValueError(
                    "Only failed, blocked, or cancelled jobs can be retried automatically; "
                    f"a succeeded job requires an explicit --from-stage (current: {manifest['status']})"
                )
            candidate = from_stage or (manifest.get("error") or {}).get("stage")
            if not candidate:
                candidate = next(
                    (
                        stage["key"]
                        for stage in manifest["stages"]
                        if stage["status"] in {"failed", "blocked", "cancelled", "pending"}
                    ),
                    None,
                )
            if candidate not in STAGE_BY_KEY:
                raise ValueError(f"Invalid retry stage: {candidate}")
            start_index = next(
                stage["index"] for stage in manifest["stages"] if stage["key"] == candidate
            )
            selected = next(
                stage for stage in manifest["stages"] if stage["key"] == candidate
            )
            if selected["status"] == "skipped":
                raise ValueError(f"Cannot retry from a skipped stage: {candidate}")

            for stage in manifest["stages"]:
                if stage["index"] < start_index or stage["status"] == "skipped":
                    continue
                stage["status"] = "pending"
                stage["progressPercent"] = 0.0
                stage["startedAt"] = None
                stage["finishedAt"] = None
                stage["durationSeconds"] = None
                stage["error"] = None

            manifest["status"] = "queued"
            manifest["currentStage"] = None
            manifest["startedAt"] = None
            manifest["finishedAt"] = None
            manifest["durationSeconds"] = None
            manifest["error"] = None
            manifest["retryCount"] = int(manifest.get("retryCount", 0)) + 1
            manifest["updatedAt"] = now_iso()
            manifest["progressPercent"] = calculate_progress(manifest["stages"])
            self._validate_manifest(manifest)
            atomic_write_json(self._job_directory(job_id) / "job.json", manifest)
            marker = self._job_directory(job_id) / "cancel.requested.json"
            try:
                marker.unlink()
            except FileNotFoundError:
                pass
        self._append_event(
            job_id,
            "job.retry-prepared",
            {"fromStage": candidate, "retryCount": manifest["retryCount"]},
        )
        self.log(job_id, f"Retry prepared from stage {candidate}", level="WARNING")
        return manifest

    def cancel_requested(self, reference: str) -> bool:
        job_id = self.resolve_job_id(reference)
        return (self._job_directory(job_id) / "cancel.requested.json").is_file()

    def log(
        self,
        reference: str,
        message: str,
        level: str = "INFO",
        stage: str | None = None,
    ) -> None:
        job_id = self.resolve_job_id(reference)
        directory = self._job_directory(job_id)
        clean_message = message.replace("\r", " ").replace("\n", "\\n")
        line = f"{now_iso()} {level.upper():<7} {stage or '-':<24} {clean_message}\n"
        targets = [directory / "logs" / "pipeline.log"]
        if stage:
            targets.append(directory / "logs" / f"{stage}.log")
        for target in targets:
            with target.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(line)
                handle.flush()

    def events_path(self, reference: str) -> Path:
        job_id = self.resolve_job_id(reference)
        return self._job_directory(job_id) / "events.jsonl"

    def logs_path(self, reference: str, stage: str | None = None) -> Path:
        job_id = self.resolve_job_id(reference)
        if stage and stage not in STAGE_BY_KEY:
            raise ValueError(f"Stage does not exist: {stage}")
        name = f"{stage}.log" if stage else "pipeline.log"
        return self._job_directory(job_id) / "logs" / name

    def _append_event(self, job_id: str, event_type: str, data: dict[str, Any]) -> None:
        append_json_line(
            self._job_directory(job_id) / "events.jsonl",
            {
                "schemaVersion": 1,
                "eventId": uuid.uuid4().hex,
                "timestamp": now_iso(),
                "jobId": job_id,
                "type": event_type,
                "data": data,
            },
        )

    @staticmethod
    def _validate_manifest(manifest: dict[str, Any]) -> None:
        required = {"schemaVersion", "jobId", "status", "stages", "progressPercent"}
        missing = required - manifest.keys()
        if missing:
            raise ValueError("Job manifest is missing fields: " + ", ".join(sorted(missing)))
        if manifest["schemaVersion"] != JOB_SCHEMA_VERSION:
            raise ValueError(f"Unsupported job schema: {manifest['schemaVersion']}")
        if manifest["status"] not in VALID_JOB_STATUSES:
            raise ValueError(f"Invalid job status: {manifest['status']}")
        if not isinstance(manifest["stages"], list):
            raise ValueError("Job stages must be a list")
        for stage in manifest["stages"]:
            if stage.get("key") not in STAGE_BY_KEY:
                raise ValueError(f"Unknown stage in manifest: {stage.get('key')}")
            if stage.get("status") not in VALID_STAGE_STATUSES:
                raise ValueError(f"Invalid stage status: {stage.get('status')}")
