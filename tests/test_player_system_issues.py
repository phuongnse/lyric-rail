from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import types

import pytest

from lyricrail import __main__ as cli


ROOT = Path(__file__).resolve().parents[1]
ISSUES = (ROOT / "apps/player/src-tauri/src/issues.rs").read_text(encoding="utf-8")
INSTALLER = (ROOT / "apps/player/src-tauri/src/model_installer.rs").read_text(
    encoding="utf-8"
)
INSTALLER_RUNTIME = INSTALLER.split("#[cfg(test)]", 1)[0]
PROCESSING = (ROOT / "apps/player/src-tauri/src/processing.rs").read_text(
    encoding="utf-8"
)
CATALOG = (ROOT / "apps/player/src-tauri/src/catalog.rs").read_text(encoding="utf-8")
RUNTIME = (ROOT / "apps/player/src-tauri/src/runtime.rs").read_text(encoding="utf-8")
PYTHON = (ROOT / "src/lyricrail/__main__.py").read_text(encoding="utf-8")
MODEL_SCRIPT = (ROOT / "scripts/install_models.py").read_text(encoding="utf-8")
APP = (ROOT / "apps/player/src/App.tsx").read_text(encoding="utf-8")
CSS = (ROOT / "apps/player/src/App.css").read_text(encoding="utf-8")
FOCUS = (ROOT / "apps/player/src/focus.ts").read_text(encoding="utf-8")
FOCUS_TEST = (ROOT / "apps/player/src/focus.test.tsx").read_text(encoding="utf-8")


def test_native_issue_contract_is_typed_bounded_and_deduplicated() -> None:
    for text in (
        "pub enum IssueSeverity",
        "pub enum IssueState",
        "pub enum IssueResolution",
        "pub struct IssueAction",
        "pub struct SystemIssue",
        "const MAX_ISSUES: usize = 100",
        "const MAX_DETAIL_CHARS: usize = 4_000",
        "existing.occurrences.saturating_add(1)",
        "redact_detail(&value)",
        'pub const ISSUES_EVENT: &str = "system-issues-changed"',
    ):
        assert text in ISSUES


def test_issue_task_output_link_is_generic_and_producer_owned() -> None:
    assert "pub related_task_id: Option<String>" in ISSUES
    assert "related_task_id: Some(item_id.to_owned())" in ISSUES
    assert "Some(MODEL_TASK_ID)" in INSTALLER_RUNTIME
    assert 'issue.code === "processing.models-missing"' not in APP
    assert "issue.relatedTaskId" in APP
    assert 'invoke<TaskRecord | null>("task_record", { taskId })' in APP
    assert 'invoke<ClearTaskHistoryResult>("clear_task_history")' not in APP


def test_missing_models_are_setup_required_with_an_allowlisted_resolver() -> None:
    assert '"PROCESSING_MODELS_MISSING"' in PYTHON
    assert "ItemStatus::SetupRequired" in PROCESSING
    assert "SetupRequired" in CATALOG
    assert "const CATALOG_SCHEMA: u16 = 3" in CATALOG
    assert "migrate_legacy_runtime_failures_to_setup_required" in CATALOG
    assert "setup_required_items" in CATALOG
    assert "queue_setup_required_after_verification" in CATALOG
    assert "retry_setup_required" in PROCESSING
    assert 'MODELS_MISSING_CODE: &str = "processing.models-missing"' in ISSUES
    assert "IssueResolution::InstallModels" in ISSUES
    assert "model_files_present_hint" in RUNTIME
    assert "resolve_runtime().map(|_| ())" in RUNTIME
    assert "runtime_repair_issue(&error)" in (ROOT / "apps/player/src-tauri/src/lib.rs").read_text(encoding="utf-8")


def test_worker_emits_a_structured_missing_model_startup_error(
    monkeypatch, capsys, tmp_path: Path
) -> None:
    monkeypatch.setattr(cli, "_root", lambda _args: tmp_path)
    monkeypatch.setattr(
        cli,
        "validate_project",
        lambda _root: {"valid": True, "summary": {"errors": 0}},
    )
    monkeypatch.setattr(cli, "load_project_config", lambda _root: {"pipeline": {}})

    def missing(*_args, **_kwargs):
        raise ValueError("Model provenance gate failed: pinned files missing")

    monkeypatch.setattr(cli, "assert_model_provenance", missing)
    assert cli._worker(argparse.Namespace(root=tmp_path)) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["kind"] == "lyricrail.worker.fatal"
    assert payload["error"]["code"] == "PROCESSING_MODELS_MISSING"


def test_model_install_is_fixed_bounded_cancellable_and_signed_runtime_safe() -> None:
    for text in (
        'runtime.integrity == "development-unverified"',
        'runtime.root.join("scripts/install_models.py")',
        'arg("--json-lines")',
        "MAX_INSTALL_OUTPUT_BYTES",
        "MAX_INSTALL_LINE_BYTES",
        "AtomicBool",
        "stop_child(&mut child)",
        "JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE",
        "BELOW_NORMAL_PRIORITY_CLASS",
        "A model installation is already running",
        "processing::retry_setup_required(&app)",
        "issues::resolution_failed",
    ):
        assert text in INSTALLER_RUNTIME
    assert "Command::new(&runtime.python)" in INSTALLER_RUNTIME
    assert "cmd /c" not in INSTALLER_RUNTIME.lower()
    assert "powershell" not in INSTALLER_RUNTIME.lower()
    assert "shell" not in INSTALLER_RUNTIME.lower()
    assert 'integrity("signed-verified")' not in INSTALLER
    assert "signed_runtime_model_mutation_is_never_allowed" in INSTALLER
    assert "controlled_child_proves_progress_line_bounds_and_cancellation" in INSTALLER
    assert "closing_the_job_terminates_the_installer_process_tree" in INSTALLER
    assert "one_active_install_is_enforced_before_process_launch" in INSTALLER
    assert "assert_model_provenance" in MODEL_SCRIPT
    assert 'parser.add_argument("--json-lines"' in MODEL_SCRIPT
    assert "download_model_and_data" not in MODEL_SCRIPT
    assert "tempfile.mkstemp" in MODEL_SCRIPT
    assert "os.replace(temporary, destination)" in MODEL_SCRIPT
    assert 'response.headers.get("Content-Length")' in MODEL_SCRIPT
    assert "downloaded > expected_size" in MODEL_SCRIPT
    runtime_core = (ROOT / "crates/lrail-format/src/runtime.rs").read_text(encoding="utf-8")
    assert 'root.join("models/audio-separator/model.ckpt")' in runtime_core
    assert 'b"tampered-model"' in runtime_core


def test_model_installer_verifies_only_after_all_controlled_downloads(
    monkeypatch, capsys, tmp_path: Path
) -> None:
    spec = importlib.util.spec_from_file_location(
        "lyricrail_test_install_models", ROOT / "scripts/install_models.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    events: list[str] = []

    hub_module = types.ModuleType("huggingface_hub")

    def snapshot_download(*, repo_id: str, revision: str, cache_dir: Path) -> None:
        del cache_dir
        events.append(f"snapshot:{repo_id}@{revision}")

    hub_module.snapshot_download = snapshot_download
    monkeypatch.setitem(sys.modules, "huggingface_hub", hub_module)
    monkeypatch.setattr(module, "PROJECT_ROOT", tmp_path)
    complete = b"complete verified checkpoint"
    digest = hashlib.sha256(complete).hexdigest()
    model_directory = tmp_path / "models" / "audio-separator"
    model_directory.mkdir(parents=True)
    model_path = model_directory / "model.ckpt"
    model_path.write_bytes(b"interrupted partial")
    monkeypatch.setattr(
        module,
        "load_model_manifest",
        lambda _root: {
            "models": {
                "separator": {
                    "type": "audio-separator-checkpoint",
                    "filename": "model.ckpt",
                    "sha256": digest,
                    "downloadUrls": {
                        "model.ckpt": "https://github.com/nomadkaraoke/python-audio-separator/releases/download/model-configs/model.ckpt"
                    },
                    "fileSizeBytes": {"model.ckpt": len(complete)},
                },
                "aligner": {
                    "type": "huggingface-snapshot",
                    "repository": "owner/model",
                    "revision": "abc",
                },
            }
        },
    )
    monkeypatch.setattr(module, "load_project_config", lambda _root: {"pipeline": {}})
    monkeypatch.setattr(
        module,
        "verify_model_provenance",
        lambda *_args, **_kwargs: {"valid": True, "errors": []},
    )

    class Response:
        def __init__(self, payload: bytes):
            self.payload = payload
            self.offset = 0
            self.headers = {"Content-Length": str(len(payload))}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, amount: int) -> bytes:
            chunk = self.payload[self.offset : self.offset + amount]
            self.offset += len(chunk)
            return chunk

    def open_download(_request):
        events.append("download:model.ckpt")
        return Response(complete)

    monkeypatch.setattr(module, "_open_download", open_download)

    def verify(_root, _pipeline, *, verify_hashes: bool):
        assert verify_hashes
        events.append("verify")
        return {"checks": [{}, {}]}

    monkeypatch.setattr(module, "assert_model_provenance", verify)
    assert module.main(["--json-lines"]) == 0
    assert events == ["download:model.ckpt", "snapshot:owner/model@abc", "verify"]
    assert model_path.read_bytes() == complete
    assert not list(model_directory.glob("*.part"))
    payloads = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert any("Repairing incomplete cached file" in payload["message"] for payload in payloads)
    assert payloads[-1]["progressPercent"] == 100.0
    assert payloads[-1]["message"] == "Verified 2 pinned models"


def test_atomic_model_download_preserves_existing_file_on_hostile_response(
    monkeypatch, tmp_path: Path
) -> None:
    spec = importlib.util.spec_from_file_location(
        "lyricrail_test_atomic_model_download", ROOT / "scripts/install_models.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    destination = tmp_path / "model.ckpt"
    expected = b"expected verified bytes"
    hostile = b"x" * len(expected)
    expected_hash = hashlib.sha256(expected).hexdigest()
    destination.write_bytes(expected)
    assert module._cached_file_matches(destination, len(expected), expected_hash)
    destination.write_bytes(b"existing partial")

    def unexpected_open(_request):
        raise AssertionError("unsafe metadata reached the network")

    monkeypatch.setattr(module, "_open_download", unexpected_open)
    for unsafe_filename, unsafe_url in (
        (
            "C:escape.ckpt",
            "https://github.com/nomadkaraoke/python-audio-separator/releases/download/model-configs/C:escape.ckpt",
        ),
        (
            destination.name,
            "https://github.com/evil/repository/releases/download/model-configs/model.ckpt",
        ),
    ):
        with pytest.raises(ValueError, match="metadata is invalid"):
            module._download_verified_file(
                key="primary",
                filename=unsafe_filename,
                url=unsafe_url,
                expected_size=len(expected),
                expected_sha256=expected_hash,
                destination=destination,
                json_lines=False,
                progress_percent=0.0,
            )
    assert destination.read_bytes() == b"existing partial"

    class WrongLengthResponse:
        headers = {"Content-Length": str(len(expected) + 1)}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(module, "_open_download", lambda _request: WrongLengthResponse())
    with pytest.raises(ValueError, match="length mismatch"):
        module._download_verified_file(
            key="primary",
            filename=destination.name,
            url="https://github.com/nomadkaraoke/python-audio-separator/releases/download/model-configs/model.ckpt",
            expected_size=len(expected),
            expected_sha256=expected_hash,
            destination=destination,
            json_lines=False,
            progress_percent=0.0,
        )
    assert destination.read_bytes() == b"existing partial"

    class CloseFailureResponse:
        def __init__(self):
            self.offset = 0
            self.headers = {"Content-Length": str(len(expected))}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            raise OSError("close failed")

        def read(self, amount: int) -> bytes:
            chunk = expected[self.offset : self.offset + amount]
            self.offset += len(chunk)
            return chunk

    monkeypatch.setattr(module, "_open_download", lambda _request: CloseFailureResponse())
    with pytest.raises(OSError, match="close failed"):
        module._download_verified_file(
            key="primary",
            filename=destination.name,
            url="https://github.com/nomadkaraoke/python-audio-separator/releases/download/model-configs/model.ckpt",
            expected_size=len(expected),
            expected_sha256=expected_hash,
            destination=destination,
            json_lines=False,
            progress_percent=0.0,
        )
    assert destination.read_bytes() == b"existing partial"
    assert not list(tmp_path.glob("*.part"))

    class Response:
        headers = {"Content-Length": str(len(hostile))}
        offset = 0

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, amount: int) -> bytes:
            chunk = hostile[self.offset : self.offset + amount]
            self.offset += len(chunk)
            return chunk

    monkeypatch.setattr(module, "_open_download", lambda _request: Response())
    with pytest.raises(ValueError, match="hash mismatch"):
        module._download_verified_file(
            key="primary",
            filename=destination.name,
            url="https://github.com/nomadkaraoke/python-audio-separator/releases/download/model-configs/model.ckpt",
            expected_size=len(expected),
            expected_sha256=expected_hash,
            destination=destination,
            json_lines=False,
            progress_percent=0.0,
        )
    assert destination.read_bytes() == b"existing partial"
    assert not list(tmp_path.glob("*.part"))


def test_activity_center_has_one_styled_accessible_resolution_flow() -> None:
    for text in (
        'className={`issues-drawer activity-drawer ${open ? "open" : ""}`}',
        'aria-live={tab === "issues" ? "polite" : "off"}',
        'aria-label="Activity views"',
        '>Tasks <b>{runningTotal}</b>',
        "Technical details",
        "Copy diagnostics",
        "View output",
        "Install pinned models?",
        "CC-BY-NC-4.0",
        'invoke("install_processing_models"',
        'invoke("cancel_task"',
        "Everything looks good",
        "View issue",
    ):
        assert text in APP
    assert '>History <b>' not in APP
    assert "Clear history" not in APP
    for selector in (
        ".issues-toggle",
        ".issues-drawer",
        ".activity-tabs",
        ".task-card",
        ".task-output-viewport",
        ".issue-card",
        ".issue-resolving",
        ".setup-dialog",
        ".about-dialog",
    ):
        assert selector in CSS
    assert "error-toast" not in APP
    assert "setError(" not in APP
    assert "useFocusContainment" in APP
    assert "container.addEventListener(\"keydown\", contain)" in FOCUS
    assert "restore.focus()" in FOCUS
    assert "contains forward and reverse Tab then restores the trigger" in FOCUS_TEST
    assert 'inert={systemModalOpen}' in APP
    assert "shouldShowIssueNotice(anyModalOpen, issuesOpen" in APP
    assert ".issue-toast { position: fixed; z-index: 35" in CSS
    assert ".modal-layer { position: fixed; z-index: 40" in CSS
