from __future__ import annotations

import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol

from .job import JobStore, now_iso


class StageHandler(Protocol):
    def __call__(self, context: "StageContext") -> list[dict[str, Any]] | None: ...


UpdateCallback = Callable[[dict[str, Any], dict[str, Any] | None], None]


class JobCancellationRequested(Exception):
    """Raised by a cooperative stage checkpoint after cancel was requested."""


@dataclass
class StageContext:
    store: JobStore
    job_id: str
    stage_key: str
    job_directory: Path

    def progress(self, percent: float, message: str = "") -> None:
        self.store.update_stage(
            self.job_id, self.stage_key, status="running", progress_percent=percent
        )
        if message:
            self.store.log(self.job_id, message, stage=self.stage_key)

    def log(self, message: str, level: str = "INFO") -> None:
        self.store.log(self.job_id, message, level=level, stage=self.stage_key)

    @property
    def cancel_requested(self) -> bool:
        return self.store.cancel_requested(self.job_id)

    def checkpoint(self) -> None:
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
    ):
        self.store = store
        self.handlers = handlers or {}
        self.on_update = on_update

    def _notify(self, job: dict[str, Any], stage: dict[str, Any] | None = None) -> None:
        if self.on_update:
            self.on_update(job, stage)

    def run(self, reference: str) -> dict[str, Any]:
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
        self.store.log(job_id, "Pipeline started")
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
                self.store.log(
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
            self.store.log(job_id, "Stage started", stage=stage["key"])
            self._notify(job, current)
            directory = Path(job["paths"]["jobDirectory"])
            context = StageContext(self.store, job_id, stage["key"], directory)
            context.work_directory.mkdir(parents=True, exist_ok=True)

            try:
                artifacts = handler(context) or []
            except (KeyboardInterrupt, JobCancellationRequested):
                return self._cancel(job_id, stage["key"])
            except Exception as exc:  # noqa: BLE001 - boundary converts engine failures
                error = {
                    "code": "STAGE_EXECUTION_FAILED",
                    "message": str(exc) or exc.__class__.__name__,
                    "stage": stage["key"],
                    "retryable": True,
                    "exceptionType": exc.__class__.__name__,
                }
                self.store.log(
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
                merged = list(latest.get("artifacts", [])) + artifacts
                self.store.update_job(job_id, artifacts=merged)
            job = self.store.update_stage(
                job_id, stage["key"], status="succeeded", progress_percent=100
            )
            current = next(item for item in job["stages"] if item["key"] == stage["key"])
            self.store.log(job_id, "Stage succeeded", stage=stage["key"])
            self._notify(job, current)

        final = self.store.update_job(
            job_id,
            status="succeeded",
            finishedAt=now_iso(),
            currentStage=None,
            error=None,
        )
        self.store.log(job_id, "Pipeline succeeded")
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
        self.store.log(job_id, "Pipeline cancelled", level="WARNING")
        self._notify(final)
        return final
