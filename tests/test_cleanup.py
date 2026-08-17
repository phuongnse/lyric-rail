from __future__ import annotations

import json
from pathlib import Path

import pytest

from lyricrail import local_pipeline


class _Store:
    def __init__(self, job: dict) -> None:
        self.job = job

    def load(self, _job_id: str) -> dict:
        return self.job


class _Context:
    def __init__(self, job_directory: Path, job: dict) -> None:
        self.job_directory = job_directory
        self.artifacts_directory = job_directory / "artifacts"
        self.job_id = str(job["jobId"])
        self.stage_key = "cleanup_intermediates"
        self.store = _Store(job)
        self.messages: list[str] = []

    def progress(self, _percent: float, message: str = "") -> None:
        if message:
            self.messages.append(message)

    def log(self, message: str, _level: str = "INFO") -> None:
        self.messages.append(message)

    def checkpoint(self) -> None:
        return None


def test_cleanup_is_scoped_and_requires_package_verification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output_root = tmp_path / "output"
    job_directory = output_root / "job-123"
    package = output_root / "Song.lrail"
    package.parent.mkdir(parents=True)
    package.write_bytes(b"encrypted-package")
    for relative in ("work/stem.flac", "inputs/lyrics.txt", "artifacts/video.mp4"):
        path = job_directory / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"cleartext")
    unrelated = output_root / "another-job" / "keep.txt"
    unrelated.parent.mkdir(parents=True)
    unrelated.write_text("keep", encoding="utf-8")
    job = {
        "jobId": "job-123",
        "stages": [{"key": "package_lrail", "status": "succeeded"}],
        "artifacts": [{"kind": "lrail-package", "path": str(package)}],
    }
    context = _Context(job_directory, job)
    commands: list[list[str]] = []
    monkeypatch.setattr(local_pipeline, "_lrail_cli", lambda _root: "lrail")
    monkeypatch.setattr(
        local_pipeline,
        "_run",
        lambda _context, command, progress=None: commands.append(command),
    )

    artifacts = local_pipeline._cleanup_verified_intermediates(context)

    assert commands == [["lrail", "verify", str(package.resolve())]]
    assert package.is_file()
    assert unrelated.read_text(encoding="utf-8") == "keep"
    assert not (job_directory / "work").exists()
    assert not (job_directory / "inputs").exists()
    assert not (job_directory / "artifacts").exists()
    report_path = job_directory / "logs" / "cleanup-report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["verifiedBeforeCleanup"] is True
    assert report["secureErasureClaimed"] is False
    assert artifacts[0]["kind"] == "cleanup-report"


def test_cleanup_refuses_package_inside_job(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    job_directory = tmp_path / "output" / "job-123"
    package = job_directory / "artifacts" / "unsafe.lrail"
    package.parent.mkdir(parents=True)
    package.write_bytes(b"encrypted-package")
    job = {
        "jobId": "job-123",
        "stages": [{"key": "package_lrail", "status": "succeeded"}],
        "artifacts": [{"kind": "lrail-package", "path": str(package)}],
    }
    context = _Context(job_directory, job)
    monkeypatch.setattr(local_pipeline, "_lrail_cli", lambda _root: "lrail")

    with pytest.raises(RuntimeError, match="outside the expected output boundary"):
        local_pipeline._cleanup_verified_intermediates(context)

    assert package.is_file()
