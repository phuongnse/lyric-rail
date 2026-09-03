from __future__ import annotations

import os
import traceback
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol

from .job import JobStore, now_iso, redact_diagnostic_text


class StageHandler(Protocol):
    def __call__(self, context: "StageContext") -> list[dict[str, Any]] | None: ...


UpdateCallback = Callable[[dict[str, Any], dict[str, Any] | None], None]
OutputCallback = Callable[[dict[str, Any]], None]


def merge_artifacts(
    existing: list[dict[str, Any]], incoming: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Replace the same durable artifact identity instead of duplicating it on retry."""
    merged: list[dict[str, Any]] = []
    indexes: dict[tuple[str, str], int] = {}
    for artifact in [*existing, *incoming]:
        kind = str(artifact.get("kind", ""))
        path = str(artifact.get("path", ""))
        identity = (kind, path) if kind and path else None
        if identity is not None and identity in indexes:
            merged[indexes[identity]] = artifact
        else:
            if identity is not None:
                indexes[identity] = len(merged)
            merged.append(artifact)
    return merged


class JobCancellationRequested(Exception):
    """Raised by a cooperative stage checkpoint after cancel was requested."""


@dataclass
class StageContext:
    store: JobStore
    job_id: str
    stage_key: str
    job_directory: Path
    notify: UpdateCallback | None = None
    output: OutputCallback | None = None
    _last_notification: float = field(default=0.0, init=False)

    def _yield_to_playback(self) -> None:
        state_path = os.environ.get("LYRICRAIL_PLAYBACK_STATE_FILE")
        if not state_path:
            return
        try:
            if Path(state_path).read_text(encoding="ascii").strip() == "playing":
                time.sleep(0.025)
        except OSError:
            pass

    def progress(self, percent: float, message: str = "") -> None:
        self._yield_to_playback()
        job = self.store.update_stage(
            self.job_id, self.stage_key, status="running", progress_percent=percent
        )
        now = time.monotonic()
        if self.notify and (now - self._last_notification >= 0.2 or percent >= 100):
            stage = next(
                item for item in job["stages"] if item["key"] == self.stage_key
            )
            self.notify(job, stage)
            self._last_notification = now
        if message:
            self.output_line(message, stream="progress")

    def log(self, message: str, level: str = "INFO") -> None:
        self.output_line(
            message,
            stream="stderr" if level.upper() in {"ERROR", "WARNING"} else "stdout",
            level=level,
        )

    def output_line(
        self, message: str, *, stream: str = "stdout", level: str = "INFO"
    ) -> None:
        message = self.store.log(self.job_id, message, level=level, stage=self.stage_key)
        self.emit_output(
            message,
            stream=stream,
            level=level,
        )

    def emit_output(
        self, message: str, *, stream: str = "stdout", level: str = "INFO"
    ) -> None:
        if self.output:
            self.output(
                {
                    "timestamp": now_iso(),
                    "stream": stream,
                    "level": level.upper(),
                    "stage": self.stage_key,
                    "text": message,
                }
            )

    @property
    def cancel_requested(self) -> bool:
        return self.store.cancel_requested(self.job_id)

    def checkpoint(self) -> None:
        self._yield_to_playback()
        if self.cancel_requested:
            raise JobCancellationRequested("cancel requested")

    @property
    def work_directory(self) -> Path:
        return self.job_directory / "work" / self.stage_key

    @property
    def artifacts_directory(self) -> Path:
        return self.job_directory / "artifacts"


class PipelineRunner:
    def __init__(
        self,
        store: JobStore,
        handlers: dict[str, StageHandler] | None = None,
        on_update: UpdateCallback | None = None,
        on_output: OutputCallback | None = None,
    ):
        self.store = store
        self.handlers = handlers or {}
        self.on_update = on_update
        self.on_output = on_output

    def _notify(self, job: dict[str, Any], stage: dict[str, Any] | None = None) -> None:
        if self.on_update:
            self.on_update(job, stage)

    def _log(
        self, job_id: str, message: str, *, level: str = "INFO", stage: str | None = None
    ) -> None:
        message = self.store.log(job_id, message, level=level, stage=stage)
        if self.on_output:
            self.on_output(
                {
                    "timestamp": now_iso(),
                    "stream": "stderr" if level.upper() in {"ERROR", "WARNING"} else "stdout",
                    "level": level.upper(),
                    "stage": stage,
                    "text": message,
                }
            )

    def run(self, reference: str) -> dict[str, Any]:
        with self.store.run_lease(reference):
            return self.run_claimed(reference)

    def run_claimed(self, reference: str) -> dict[str, Any]:
        """Run while the caller holds this job's exclusive run lease."""
        job = self.store.load(reference)
        job_id = job["jobId"]
        if job["status"] not in {"queued", "blocked"}:
            raise ValueError(f"Cannot run a job in status {job['status']}")

        started_at = job.get("startedAt") or now_iso()
        job = self.store.update_job(
            job_id,
            status="running",
            startedAt=started_at,
            finishedAt=None,
            error=None,
        )
        self._log(job_id, "Pipeline started")
        self._notify(job)

        for stage in job["stages"]:
            if stage["status"] in {"skipped", "succeeded"}:
                continue
            if self.store.cancel_requested(job_id):
                return self._cancel(job_id, stage["key"])

            handler = self.handlers.get(stage["key"])
            if handler is None:
                error = {
                    "code": "STAGE_HANDLER_MISSING",
                    "message": f"No engine is installed for stage '{stage['key']}'.",
                    "stage": stage["key"],
                    "retryable": False,
                    "hint": "Install the corresponding engine or implement the handler before production use.",
                }
                blocked = self.store.update_stage(
                    job_id, stage["key"], status="blocked", error=error
                )
                self._log(
                    job_id, error["message"], level="ERROR", stage=stage["key"]
                )
                final = self.store.update_job(
                    job_id,
                    status="blocked",
                    finishedAt=now_iso(),
                    currentStage=None,
                    error=error,
                )
                current = next(
                    item for item in blocked["stages"] if item["key"] == stage["key"]
                )
                self._notify(final, current)
                return final

            job = self.store.update_stage(
                job_id, stage["key"], status="running", progress_percent=0
            )
            current = next(item for item in job["stages"] if item["key"] == stage["key"])
            self._log(job_id, "Stage started", stage=stage["key"])
            self._notify(job, current)
            directory = Path(job["paths"]["jobDirectory"])
            context = StageContext(
                self.store,
                job_id,
                stage["key"],
                directory,
                self.on_update,
                self.on_output,
            )
            context.work_directory.mkdir(parents=True, exist_ok=True)

            try:
                artifacts = handler(context) or []
            except (KeyboardInterrupt, JobCancellationRequested):
                return self._cancel(job_id, stage["key"])
            except Exception as exc:  # noqa: BLE001 - boundary converts engine failures
                error = {
                    "code": "STAGE_EXECUTION_FAILED",
                    "message": redact_diagnostic_text(
                        str(exc) or exc.__class__.__name__
                    ),
                    "stage": stage["key"],
                    "retryable": True,
                    "exceptionType": exc.__class__.__name__,
                }
                self._log(
                    job_id,
                    traceback.format_exc(),
                    level="ERROR",
                    stage=stage["key"],
                )
                failed = self.store.update_stage(
                    job_id, stage["key"], status="failed", error=error
                )
                final = self.store.update_job(
                    job_id,
                    status="failed",
                    finishedAt=now_iso(),
                    currentStage=None,
                    error=error,
                )
                current = next(
                    item for item in failed["stages"] if item["key"] == stage["key"]
                )
                self._notify(final, current)
                return final

            if artifacts:
                latest = self.store.load(job_id)
                merged = merge_artifacts(list(latest.get("artifacts", [])), artifacts)
                self.store.update_job(job_id, artifacts=merged)
            job = self.store.update_stage(
                job_id, stage["key"], status="succeeded", progress_percent=100
            )
            current = next(item for item in job["stages"] if item["key"] == stage["key"])
            self._log(job_id, "Stage succeeded", stage=stage["key"])
            self._notify(job, current)

        final = self.store.update_job(
            job_id,
            status="succeeded",
            finishedAt=now_iso(),
            currentStage=None,
            error=None,
        )
        self._log(job_id, "Pipeline succeeded")
        self._notify(final)
        return final

    def _cancel(self, job_id: str, stage_key: str) -> dict[str, Any]:
        current = self.store.load(job_id)
        stage = next(item for item in current["stages"] if item["key"] == stage_key)
        if stage["status"] in {"pending", "running"}:
            self.store.update_stage(job_id, stage_key, status="cancelled")
        final = self.store.update_job(
            job_id,
            status="cancelled",
            finishedAt=now_iso(),
            currentStage=None,
            error={
                "code": "JOB_CANCELLED",
                "message": "The job was cancelled by request.",
                "stage": stage_key,
                "retryable": True,
            },
        )
        self._log(job_id, "Pipeline cancelled", level="WARNING")
        self._notify(final)
        return final
