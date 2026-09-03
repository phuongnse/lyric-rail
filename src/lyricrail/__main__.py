from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

from . import __version__
from .config import (
    load_project_config,
    load_project_environment,
    resolve_data_root,
)
from .job import (
    TERMINAL_JOB_STATUSES,
    VALID_JOB_STATUSES,
    MAX_DIAGNOSTIC_JSON_BYTES,
    JobStore,
    build_plan,
    redact_diagnostic_text,
    sanitize_diagnostic_payload,
)
from .lyric_input import AuthoritativeLyrics, load_authoritative_lyrics
from .local_pipeline import build_local_handlers, render_review_preview
from .metadata import build_local_metadata
from .model_provenance import assert_model_provenance
from .presentation import (
    clear_terminal,
    print_job_created,
    print_job_status,
    print_jobs,
    print_json,
    print_plan,
    print_validation,
    tail_lines,
)
from .runner import PipelineRunner
from .revision_alignment import align_revision_scope
from .source import ResolvedSource, parse_timecode, resolve_source
from .toolchain import collect_doctor_report, print_doctor_report
from .validation import validate_project


def _configure_standard_streams() -> None:
    """Keep Unicode content stable in consoles, redirects, and CI logs."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


def _add_root_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--root",
        type=Path,
        help="Project directory; defaults to LYRICRAIL_HOME or the current directory",
    )


def _add_json_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON")


def _timecode_argument(value: str) -> float:
    try:
        return parse_timecode(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def _add_source_arguments(parser: argparse.ArgumentParser) -> None:
    _add_root_argument(parser)
    _add_json_argument(parser)
    parser.add_argument(
        "source",
        nargs="?",
        help="Local audio/video file; omit to use input/",
    )
    parser.add_argument(
        "--lyrics",
        type=Path,
        required=True,
        help="UTF-8 text file containing the exact lyrics, one semantic phrase per line",
    )
    parser.add_argument(
        "--start",
        type=_timecode_argument,
        help="Song start in the source: seconds, MM:SS, or HH:MM:SS",
    )
    parser.add_argument(
        "--end",
        type=_timecode_argument,
        help="Song end in the source: seconds, MM:SS, or HH:MM:SS",
    )
    parser.add_argument("--title", help="Override the song title for output and metadata")
    parser.add_argument("--artist", help="Override the reference artist for metadata")
    parser.add_argument("--composer", help="Optional composer metadata")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lyricrail",
        description="Cross-platform karaoke production and encrypted LyricRail packaging.",
    )
    parser.add_argument("--version", action="version", version=f"LyricRail {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    doctor = subparsers.add_parser("doctor", help="Check the runtime and toolchain")
    _add_root_argument(doctor)
    _add_json_argument(doctor)
    doctor.add_argument(
        "--production",
        action="store_true",
        help="Also hash every active checkpoint and require exact model snapshots",
    )

    config = subparsers.add_parser("config", help="Validate or inspect configuration")
    _add_root_argument(config)
    _add_json_argument(config)
    config.add_argument("action", choices=("validate", "show"), nargs="?", default="validate")

    plan = subparsers.add_parser("plan", help="Show the processing plan without creating a job")
    _add_source_arguments(plan)

    run = subparsers.add_parser("run", help="Create and execute a job")
    _add_source_arguments(run)
    run.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the processing plan without writing output",
    )

    jobs = subparsers.add_parser("jobs", help="List recent jobs")
    _add_root_argument(jobs)
    _add_json_argument(jobs)
    jobs.add_argument("--limit", type=int, default=20)
    jobs.add_argument(
        "--status",
        action="append",
        choices=sorted(VALID_JOB_STATUSES),
        help="Filter by status; may be repeated",
    )

    status = subparsers.add_parser("status", help="Show job status")
    _add_root_argument(status)
    _add_json_argument(status)
    status.add_argument("job", nargs="?", default="latest", help="Job ID or latest")
    status.add_argument("--watch", action="store_true", help="Watch until the job reaches a terminal state")
    status.add_argument("--interval", type=float, default=2.0)

    logs = subparsers.add_parser("logs", help="Show pipeline or stage logs")
    _add_root_argument(logs)
    logs.add_argument("job", nargs="?", default="latest", help="Job ID or latest")
    logs.add_argument("--stage", help="Show only one stage log")
    logs.add_argument("--tail", type=int, default=200)
    logs.add_argument("--follow", action="store_true")

    events = subparsers.add_parser("events", help="Show the structured event journal")
    _add_root_argument(events)
    events.add_argument("job", nargs="?", default="latest", help="Job ID or latest")
    events.add_argument("--tail", type=int, default=100)
    events.add_argument("--follow", action="store_true")

    cancel = subparsers.add_parser("cancel", help="Request cancellation of a running job")
    _add_root_argument(cancel)
    _add_json_argument(cancel)
    cancel.add_argument("job", nargs="?", default="latest", help="Job ID or latest")

    retry = subparsers.add_parser("retry", help="Prepare a retry from a failed stage")
    _add_root_argument(retry)
    _add_json_argument(retry)
    retry.add_argument("job", nargs="?", default="latest", help="Job ID or latest")
    retry.add_argument("--from-stage", help="Stage to restart from; defaults to the failed stage")
    retry.add_argument("--run", action="store_true", help="Run immediately after resetting state")

    preview = subparsers.add_parser(
        "preview", help="Render a short typography and timing review"
    )
    _add_root_argument(preview)
    _add_json_argument(preview)
    preview.add_argument("job", nargs="?", default="latest", help="Job ID or latest")
    preview.add_argument("--start", type=float, default=20.0, help="Start time within the song")
    preview.add_argument(
        "--duration", type=float, default=25.0, help="Review duration, up to 120 seconds"
    )

    worker = subparsers.add_parser(
        "worker", help="Run the persistent local processing worker over JSON lines"
    )
    _add_root_argument(worker)

    revision_align = subparsers.add_parser(
        "revision-align", help="Re-align explicitly changed package lyric lines"
    )
    _add_root_argument(revision_align)
    _add_json_argument(revision_align)
    revision_align.add_argument("--audio", type=Path, required=True)
    revision_align.add_argument("--timing", type=Path, required=True)
    revision_align.add_argument("--lyrics", type=Path, required=True)
    revision_align.add_argument("--output", type=Path, required=True)
    return parser


def _root(args: argparse.Namespace) -> Path:
    return load_project_environment(args.root)


def _validated_config(root: Path, as_json: bool) -> tuple[dict[str, Any] | None, int]:
    report = validate_project(root)
    if not report["valid"]:
        if as_json:
            print_json(report)
        else:
            print_validation(report)
        return None, 2
    return load_project_config(root), 0


def _doctor(args: argparse.Namespace) -> int:
    root = _root(args)
    report = collect_doctor_report(root, production=bool(args.production))
    print_doctor_report(report, args.json)
    return 0 if report["ready"] else 1


def _config(args: argparse.Namespace) -> int:
    root = _root(args)
    if args.action == "validate":
        report = validate_project(root)
        print_json(report) if args.json else print_validation(report)
        return 0 if report["valid"] else 2
    data = load_project_config(root)
    print_json({"kind": "lyricrail.config", "projectRoot": str(root), "config": data})
    return 0


def _resolved_source(root: Path, args: argparse.Namespace) -> ResolvedSource:
    return resolve_source(
        root,
        args.source,
        start_seconds=args.start,
        end_seconds=args.end,
        title_override=args.title,
        artist_override=args.artist,
        composer_override=args.composer,
    )


def _resolved_lyrics(args: argparse.Namespace) -> AuthoritativeLyrics:
    return load_authoritative_lyrics(args.lyrics)


def _make_plan(args: argparse.Namespace) -> tuple[dict[str, Any] | None, int, Path | None]:
    root = _root(args)
    config, exit_code = _validated_config(root, args.json)
    if config is None:
        return None, exit_code, None
    source = _resolved_source(root, args)
    lyrics = _resolved_lyrics(args)
    metadata = build_local_metadata(
        source.path,
        song_title=source.song_title,
        artist=source.artist,
        composer=source.composer,
    )
    plan = build_plan(source.path, config["pipeline"], metadata, False)
    plan["sourceInput"] = source.input_value
    plan["sourceOrigin"] = source.origin
    plan["sourceKindHint"] = source.media_kind_hint
    plan["sourceRange"] = {
        "startSeconds": source.requested_start_seconds,
        "endSeconds": source.requested_end_seconds,
    }
    plan["lyricsInput"] = {
        "mode": "authoritative-input",
        "sourcePath": str(lyrics.source_path),
        "sha256": lyrics.sha256,
        "lineCount": len(lyrics.lines),
        "wordCount": lyrics.word_count,
        "detectedTextUsed": False,
        "captionUsed": False,
    }
    validation = validate_project(root)
    plan["warnings"].extend(
        issue["message"] for issue in validation["issues"] if issue["severity"] == "warning"
    )
    return plan, 0, root


def _plan(args: argparse.Namespace) -> int:
    plan, exit_code, _ = _make_plan(args)
    if plan is None:
        return exit_code
    print_json(plan) if args.json else print_plan(plan)
    return 0


def _human_runner_callback(job: dict[str, Any], stage: dict[str, Any] | None) -> None:
    if stage is None:
        return
    symbol = {
        "running": "▶",
        "succeeded": "✓",
        "failed": "✗",
        "blocked": "!",
        "cancelled": "■",
    }.get(stage["status"], "·")
    print(
        f"{symbol} [{stage['index']:02d}/{len(job['stages']):02d}] "
        f"{stage['title']} — {stage['status']}"
    )


def _execute_job(
    args: argparse.Namespace,
    config: dict[str, Any],
    *,
    verify_models: bool,
    on_update: Any = None,
    on_output: Any = None,
    resume_job_id: str | None = None,
) -> dict[str, Any]:
    root = _root(args)
    if verify_models:
        assert_model_provenance(root, config["pipeline"], verify_hashes=True)
    source = _resolved_source(root, args)
    lyrics = _resolved_lyrics(args)
    metadata = build_local_metadata(
        source.path,
        song_title=source.song_title,
        artist=source.artist,
        composer=source.composer,
    )
    store = JobStore(resolve_data_root(root) / "output")
    runner = PipelineRunner(
        store,
        handlers=build_local_handlers(root),
        on_update=on_update,
        on_output=on_output,
    )
    if resume_job_id:
        with store.run_lease(resume_job_id):
            existing = store.load(resume_job_id)
            request = existing.get("request", {})
            fingerprint = request.get("sourceFingerprint", {})
            source_stat = source.path.stat()
            if Path(str(request.get("sourceMedia", ""))).resolve() != source.path.resolve():
                raise ValueError("Retry source path does not match the isolated job")
            if int(fingerprint.get("sizeBytes", -1)) != source_stat.st_size or int(
                fingerprint.get("modifiedNanoseconds", -1)
            ) != source_stat.st_mtime_ns or int(
                fingerprint.get("changedNanoseconds", -1)
            ) != source_stat.st_ctime_ns or int(
                fingerprint.get("device", -1)
            ) != source_stat.st_dev or int(
                fingerprint.get("fileId", -1)
            ) != source_stat.st_ino:
                raise ValueError(
                    "Retry source media changed after the successful stage artifacts were made"
                )
            if str(request.get("lyrics", {}).get("sha256", "")) != lyrics.sha256:
                raise ValueError("Retry lyrics do not match the isolated job snapshot")
            interrupted_after_all_stages = existing["status"] == "running" and all(
                stage["status"] in {"succeeded", "skipped"}
                for stage in existing["stages"]
            )
            if interrupted_after_all_stages:
                existing = store.update_job(
                    resume_job_id,
                    status="succeeded",
                    currentStage=None,
                    finishedAt=existing.get("finishedAt") or existing.get("updatedAt"),
                    error=None,
                )
            if existing["status"] == "succeeded":
                package = next(
                    (
                        Path(str(artifact.get("path", "")))
                        for artifact in existing.get("artifacts", [])
                        if artifact.get("kind") == "lrail-package"
                    ),
                    None,
                )
                if package is None or not package.is_file():
                    raise ValueError("Completed retry job has no durable package artifact")
                return existing
            job = store.prepare_retry(resume_job_id, allow_interrupted=True)
            return runner.run_claimed(job["jobId"])

    job = store.create(
        source.path,
        config["pipeline"],
        metadata,
        False,
        source_input=source.input_value,
        source_origin=source.origin,
        source_kind_hint=source.media_kind_hint,
        requested_start_seconds=source.requested_start_seconds,
        requested_end_seconds=source.requested_end_seconds,
        media_trim_start_seconds=source.media_trim_start_seconds,
        media_trim_end_seconds=source.media_trim_end_seconds,
        source_pretrimmed=source.source_pretrimmed,
        lyrics_text=lyrics.text,
        lyrics_source_path=lyrics.source_path,
        lyrics_sha256=lyrics.sha256,
        lyrics_line_count=len(lyrics.lines),
        lyrics_word_count=lyrics.word_count,
        project_root=root,
    )
    return runner.run(job["jobId"])


def _run(args: argparse.Namespace) -> int:
    if args.dry_run:
        return _plan(args)
    root = _root(args)
    config, exit_code = _validated_config(root, args.json)
    if config is None:
        return exit_code
    if not args.json:
        print(f"Processing local media: {args.source or 'input/'}")
    final = _execute_job(
        args,
        config,
        verify_models=True,
        on_update=None if args.json else _human_runner_callback,
    )
    if args.json:
        print_json(final)
    else:
        print()
        print_job_status(final)
    return {"succeeded": 0, "blocked": 3, "failed": 4, "cancelled": 130}.get(
        final["status"], 4
    )


_WORKER_CONTROL_FIELDS = {
    "kind",
    "requestId",
    "jobId",
    "status",
    "stage",
    "stageStatus",
    "outputStream",
    "packagePath",
}


def _valid_scalar_text(value: str) -> bool:
    return not any(0xD800 <= ord(character) <= 0xDFFF for character in value)


def _worker_fallback(payload: dict[str, Any], message: str) -> dict[str, Any]:
    request_id = payload.get("requestId")
    return {
        "kind": "lyricrail.worker.failed",
        "requestId": (
            request_id
            if isinstance(request_id, str) and _valid_scalar_text(request_id)
            else ""
        ),
        "error": message,
    }


def _worker_safe_payload(payload: dict[str, Any]) -> dict[str, Any]:
    safe = sanitize_diagnostic_payload(payload)
    if not isinstance(safe, dict):
        return _worker_fallback(payload, "Worker output payload is invalid")
    for field in _WORKER_CONTROL_FIELDS:
        if field not in payload:
            continue
        value = payload[field]
        if value is not None and not isinstance(value, str):
            return _worker_fallback(
                payload, f"Worker produced an invalid control field {field}"
            )
        if isinstance(value, str) and not _valid_scalar_text(value):
            return _worker_fallback(
                payload, f"Worker produced invalid Unicode in control field {field}"
            )
        safe[field] = value
    return safe


def _worker_emit(payload: dict[str, Any]) -> None:
    try:
        safe_payload = _worker_safe_payload(payload)
        line = json.dumps(
            safe_payload,
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
        )
        if len(line.encode("utf-8")) > MAX_DIAGNOSTIC_JSON_BYTES:
            line = json.dumps(
                _worker_fallback(payload, "Worker output exceeded its JSON bound"),
                ensure_ascii=True,
                allow_nan=False,
                separators=(",", ":"),
            )
    except Exception:  # noqa: BLE001 - worker output must retain a final safe fallback
        line = '{"kind":"lyricrail.worker.failed","requestId":"","error":"Worker output could not be encoded"}'
    sys.stdout.write(line + "\n")
    sys.stdout.flush()


def _worker(args: argparse.Namespace) -> int:
    root = _root(args)
    try:
        validation = validate_project(root)
        if not validation["valid"]:
            raise ValueError(
                f"Runtime configuration has {validation['summary']['errors']} errors"
            )
        config = load_project_config(root)
        assert_model_provenance(root, config["pipeline"], verify_hashes=True)
    except (OSError, RuntimeError, ValueError) as exc:
        message = str(exc)
        code = (
            "PROCESSING_MODELS_MISSING"
            if message.startswith("Model provenance gate failed:")
            else "PROCESSING_RUNTIME_STARTUP_FAILED"
        )
        _worker_emit(
            {
                "kind": "lyricrail.worker.fatal",
                "error": {"code": code, "message": message},
            }
        )
        return 2
    _worker_emit({"kind": "lyricrail.worker.ready", "schemaVersion": 1})
    for raw_line in sys.stdin:
        if len(raw_line.encode("utf-8")) > 1024 * 1024:
            _worker_emit(
                {
                    "kind": "lyricrail.worker.rejected",
                    "error": "Worker request exceeds 1 MiB",
                }
            )
            continue
        request: Any = None
        try:
            request = json.loads(raw_line)
            if not isinstance(request, dict):
                raise ValueError("Worker request must be an object")
            request_id = str(request.get("requestId", ""))
            if not request_id or len(request_id) > 180:
                raise ValueError("Worker requestId is invalid")
            namespace = argparse.Namespace(
                root=root,
                json=True,
                dry_run=False,
                source=str(request["mediaPath"]),
                lyrics=Path(str(request["lyricsPath"])),
                start=request.get("startSeconds"),
                end=request.get("endSeconds"),
                title=request.get("title"),
                artist=request.get("artist"),
                composer=request.get("composer"),
            )

            def notify(job: dict[str, Any], stage: dict[str, Any] | None) -> None:
                _worker_emit(
                    {
                        "kind": "lyricrail.worker.progress",
                        "requestId": request_id,
                        "jobId": job.get("jobId"),
                        "status": job.get("status"),
                        "progressPercent": job.get("progressPercent", 0),
                        "stage": stage.get("key") if stage else None,
                        "stageTitle": stage.get("title") if stage else None,
                        "stageProgressPercent": (
                            stage.get("progressPercent", 0) if stage else None
                        ),
                        "stageStatus": stage.get("status") if stage else None,
                    }
                )

            def output(event: dict[str, Any]) -> None:
                _worker_emit(
                    {
                        "kind": "lyricrail.worker.output",
                        "requestId": request_id,
                        "outputStream": event.get("stream", "stdout"),
                        "stage": event.get("stage"),
                        "outputText": event.get("text", ""),
                    }
                )

            final = _execute_job(
                namespace,
                config,
                verify_models=False,
                on_update=notify,
                on_output=output,
                resume_job_id=(
                    str(request.get("resumeJobId", "")).strip() or None
                ),
            )
            package = next(
                (
                    artifact.get("path")
                    for artifact in final.get("artifacts", [])
                    if artifact.get("kind") == "lrail-package"
                ),
                None,
            )
            _worker_emit(
                {
                    "kind": "lyricrail.worker.completed",
                    "requestId": request_id,
                    "jobId": final.get("jobId"),
                    "status": final.get("status"),
                    "packagePath": package,
                    "error": final.get("error"),
                }
            )
        except (KeyError, OSError, RuntimeError, ValueError) as exc:
            _worker_emit(
                {
                    "kind": "lyricrail.worker.failed",
                    "requestId": str(request.get("requestId", ""))
                    if isinstance(request, dict)
                    else "",
                    "error": str(exc),
                }
            )
    return 0


def _jobs(args: argparse.Namespace) -> int:
    root = _root(args)
    if args.limit < 1 or args.limit > 1000:
        raise ValueError("--limit must be between 1 and 1000")
    statuses = set(args.status) if args.status else None
    jobs = JobStore(resolve_data_root(root) / "output").list_jobs(args.limit, statuses)
    if args.json:
        print_json({"kind": "lyricrail.job-list", "count": len(jobs), "jobs": jobs})
    else:
        print_jobs(jobs)
    return 0


def _status(args: argparse.Namespace) -> int:
    root = _root(args)
    if args.interval < 0.25:
        raise ValueError("--interval must be at least 0.25 seconds")
    store = JobStore(resolve_data_root(root) / "output")
    while True:
        job = store.load(args.job)
        if args.json:
            print_json(job, compact=args.watch)
        else:
            if args.watch:
                clear_terminal()
            print_job_status(job)
        if not args.watch or job["status"] in TERMINAL_JOB_STATUSES:
            return 0
        time.sleep(args.interval)


def _follow_text_file(path: Path, initial_tail: int) -> int:
    for line in tail_lines(path, initial_tail):
        print(line)
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    if not initial_tail:
        position = 0
    else:
        position = path.stat().st_size
    try:
        while True:
            with path.open("r", encoding="utf-8", errors="replace") as handle:
                handle.seek(position)
                chunk = handle.read()
                position = handle.tell()
            if chunk:
                print(chunk, end="", flush=True)
            time.sleep(0.5)
    except KeyboardInterrupt:
        return 130


def _logs(args: argparse.Namespace) -> int:
    root = _root(args)
    if args.tail < 0:
        raise ValueError("--tail must not be negative")
    path = JobStore(resolve_data_root(root) / "output").logs_path(args.job, args.stage)
    if args.follow:
        job = JobStore(resolve_data_root(root) / "output").load(args.job)
        if job["status"] in TERMINAL_JOB_STATUSES:
            lines = tail_lines(path, args.tail)
            if lines:
                print("\n".join(lines))
            return 0
        return _follow_text_file(path, args.tail)
    lines = tail_lines(path, args.tail)
    if not lines:
        print(f"No log entries yet: {path}")
        return 0
    print("\n".join(lines))
    return 0


def _events(args: argparse.Namespace) -> int:
    root = _root(args)
    if args.tail < 0:
        raise ValueError("--tail must not be negative")
    path = JobStore(resolve_data_root(root) / "output").events_path(args.job)
    if args.follow:
        job = JobStore(resolve_data_root(root) / "output").load(args.job)
        if job["status"] in TERMINAL_JOB_STATUSES:
            print("\n".join(tail_lines(path, args.tail)))
            return 0
        return _follow_text_file(path, args.tail)
    print("\n".join(tail_lines(path, args.tail)))
    return 0


def _cancel(args: argparse.Namespace) -> int:
    root = _root(args)
    job = JobStore(resolve_data_root(root) / "output").request_cancel(args.job)
    print_json(job) if args.json else print_job_status(job)
    return 0


def _retry(args: argparse.Namespace) -> int:
    root = _root(args)
    store = JobStore(resolve_data_root(root) / "output")
    job = store.prepare_retry(args.job, args.from_stage)
    if not args.run:
        print_json(job) if args.json else print_job_status(job)
        return 0
    runner = PipelineRunner(
        store,
        handlers=build_local_handlers(root),
        on_update=None if args.json else _human_runner_callback,
    )
    final = runner.run(job["jobId"])
    print_json(final) if args.json else print_job_status(final)
    return {"succeeded": 0, "blocked": 3, "failed": 4, "cancelled": 130}.get(
        final["status"], 4
    )


def _preview(args: argparse.Namespace) -> int:
    root = _root(args)
    store = JobStore(resolve_data_root(root) / "output")
    job = store.load(args.job)
    output = render_review_preview(
        root,
        job,
        start_seconds=args.start,
        duration_seconds=args.duration,
    )
    payload = {
        "kind": "lyricrail.preview",
        "jobId": job["jobId"],
        "startSeconds": args.start,
        "durationSeconds": args.duration,
        "path": str(output.resolve()),
        "sizeBytes": output.stat().st_size,
    }
    if args.json:
        print_json(payload)
    else:
        print(f"Preview ready: {output}")
    return 0


def _revision_align(args: argparse.Namespace) -> int:
    payload = align_revision_scope(
        _root(args),
        args.audio.resolve(),
        args.timing.resolve(),
        args.lyrics.resolve(),
        args.output.resolve(),
    )
    print_json(payload) if args.json else print(f"Revision timing ready: {payload['output']}")
    return 0


def _error_payload(exc: Exception) -> dict[str, Any]:
    return {
        "kind": "lyricrail.error",
        "error": {
            "code": "CLI_ERROR",
            "message": redact_diagnostic_text(str(exc)),
            "type": exc.__class__.__name__,
        },
    }


def main(argv: list[str] | None = None) -> int:
    _configure_standard_streams()
    args = build_parser().parse_args(argv)
    handlers = {
        "doctor": _doctor,
        "config": _config,
        "plan": _plan,
        "run": _run,
        "jobs": _jobs,
        "status": _status,
        "logs": _logs,
        "events": _events,
        "cancel": _cancel,
        "retry": _retry,
        "preview": _preview,
        "worker": _worker,
        "revision-align": _revision_align,
    }
    try:
        return handlers[args.command](args)
    except KeyboardInterrupt:
        return 130
    except (OSError, RuntimeError, ValueError) as exc:
        if getattr(args, "json", False):
            print_json(_error_payload(exc), stream=sys.stderr)
        else:
            print(f"ERROR: {redact_diagnostic_text(str(exc))}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
