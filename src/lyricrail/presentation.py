from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, TextIO


STATUS_SYMBOLS = {
    "pending": "○",
    "queued": "○",
    "running": "▶",
    "succeeded": "✓",
    "failed": "✗",
    "blocked": "!",
    "cancelled": "■",
    "cancelling": "■",
    "skipped": "–",
    "planned": "◇",
}


def print_json(data: Any, compact: bool = False, stream: TextIO = sys.stdout) -> None:
    if compact:
        print(json.dumps(data, ensure_ascii=True, separators=(",", ":")), file=stream)
    else:
        print(json.dumps(data, ensure_ascii=True, indent=2), file=stream)


def format_duration(seconds: float | int | None) -> str:
    if seconds is None:
        return "—"
    seconds = max(0, int(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours:d}h {minutes:02d}m {seconds:02d}s"
    if minutes:
        return f"{minutes:d}m {seconds:02d}s"
    return f"{seconds:d}s"


def elapsed_seconds(job: dict[str, Any]) -> float | None:
    started = job.get("startedAt")
    if not started:
        return None
    end = job.get("finishedAt") or datetime.now().astimezone().isoformat()
    return (datetime.fromisoformat(end) - datetime.fromisoformat(started)).total_seconds()


def progress_bar(percent: float, width: int = 28) -> str:
    bounded = max(0.0, min(100.0, float(percent)))
    complete = round(width * bounded / 100.0)
    return "[" + "=" * complete + "·" * (width - complete) + f"] {bounded:6.2f}%"


def print_plan(plan: dict[str, Any]) -> None:
    print("LyricRail · PROCESSING PLAN")
    print(f"Source:   {plan['sourceVideo']}")
    print(f"Title:    {plan['metadataPreview']['title']}")
    if plan["metadataPreview"].get("artist"):
        print(f"Artist:   {plan['metadataPreview']['artist']}")
    print(f"Quality:  {plan['qualityMode']}")
    print("\nStages")
    for stage in plan["stages"]:
        symbol = STATUS_SYMBOLS[stage["status"]]
        suffix = f" — {stage['skipReason']}" if stage.get("skipReason") else ""
        print(f"  {stage['index']:02d} {symbol} {stage['title']}{suffix}")
    if plan.get("warnings"):
        print("\nWarnings")
        for warning in plan["warnings"]:
            print(f"  ! {warning}")


def print_job_created(job: dict[str, Any]) -> None:
    print(f"LyricRail job {job['jobId']}")
    print(f"Status:   {job['status']}")
    print(f"Source:   {job['request']['sourceVideo']}")
    print(f"Tracking: lyricrail status {job['jobId']} --watch")
    print(f"Logs:     lyricrail logs {job['jobId']} --follow")


def print_job_status(job: dict[str, Any]) -> None:
    print(f"LyricRail · JOB {job['jobId']}")
    print(
        f"Status:   {job['status'].upper():<12} "
        f"{progress_bar(job['progressPercent'])}"
    )
    print(f"Elapsed:  {format_duration(job.get('durationSeconds') or elapsed_seconds(job))}")
    print(f"Source:   {job['request']['sourceVideo']}")
    print("\nStages")
    for stage in job["stages"]:
        symbol = STATUS_SYMBOLS[stage["status"]]
        detail = ""
        if stage["status"] == "running":
            detail = f" {stage['progressPercent']:.1f}%"
        elif stage.get("durationSeconds") is not None:
            detail = f" {format_duration(stage['durationSeconds'])}"
        elif stage.get("skipReason"):
            detail = f" — {stage['skipReason']}"
        print(f"  {stage['index']:02d} {symbol} {stage['title']:<34}{detail}")
    if job.get("artifacts"):
        print("\nArtifacts")
        for artifact in job["artifacts"]:
            print(f"  {artifact.get('type', 'file'):<16} {artifact.get('path', '')}")
    if job.get("warnings"):
        print("\nWarnings")
        for warning in job["warnings"]:
            print(f"  ! {warning}")
    if job.get("error"):
        error = job["error"]
        print("\nError")
        print(f"  {error.get('code', 'UNKNOWN')}: {error.get('message', '')}")
        if error.get("hint"):
            print(f"  Hint: {error['hint']}")


def print_jobs(jobs: list[dict[str, Any]]) -> None:
    if not jobs:
        print("No jobs found.")
        return
    print(f"{'JOB ID':<54} {'STATUS':<11} {'PROGRESS':>8} {'UPDATED':<25}")
    for job in jobs:
        print(
            f"{job['jobId']:<54} {job['status']:<11} "
            f"{job['progressPercent']:>7.2f}% {job['updatedAt']:<25}"
        )


def print_validation(report: dict[str, Any]) -> None:
    state = "VALID" if report["valid"] else "INVALID"
    summary = report["summary"]
    print(
        f"LyricRail config: {state} "
        f"({summary['errors']} errors, {summary['warnings']} warnings)"
    )
    print(f"Project: {report['projectRoot']}")
    for issue in report["issues"]:
        print(
            f"[{issue['severity'].upper():7}] {issue['code']:<24} "
            f"{issue['location']}: {issue['message']}"
        )
        if issue.get("hint"):
            print(f"          Hint: {issue['hint']}")


def tail_lines(path: Path, count: int) -> list[str]:
    if count <= 0 or not path.is_file():
        return []
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        lines = handle.readlines()
    return [line.rstrip("\n") for line in lines[-count:]]


def clear_terminal() -> None:
    if sys.stdout.isatty():
        os.system("cls" if os.name == "nt" else "clear")
