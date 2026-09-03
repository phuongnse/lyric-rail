from __future__ import annotations

import io
import json
import sys
import tempfile
import time
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

from lyricrail import __main__ as cli
from lyricrail.job import JobStore, STAGE_SPECS, redact_diagnostic_text
from lyricrail.local_pipeline import (
    _command_duration_seconds,
    _ffmpeg_progress_seconds,
    _run,
)
from lyricrail.runner import PipelineRunner, StageContext


PIPELINE = {"pipelineVersion": 1, "quality": {"mode": "maximum"}}
ROOT = Path(__file__).resolve().parents[1]


class CommandContext:
    def __init__(self, cancel_after: float | None = None) -> None:
        self.started = time.monotonic()
        self.cancel_after = cancel_after
        self.logs: list[str] = []
        self.output: list[tuple[str, str, float]] = []
        self.progress_updates: list[float] = []

    def checkpoint(self) -> None:
        if self.cancel_after is not None and time.monotonic() - self.started >= self.cancel_after:
            raise RuntimeError("controlled cancellation")

    def log(self, message: str, level: str = "INFO") -> None:
        del level
        self.logs.append(message)

    def output_line(self, message: str, *, stream: str = "stdout", level: str = "INFO") -> None:
        del level
        self.output.append((stream, message, time.monotonic()))

    def progress(self, percent: float, message: str = "") -> None:
        del message
        self.progress_updates.append(percent)


class PlayerTaskRuntimeTests(unittest.TestCase):
    def test_worker_json_replaces_nested_surrogates_before_utf8_output(self) -> None:
        output = io.StringIO()
        with patch.object(cli.sys, "stdout", output):
            cli._worker_emit(
                {
                    "kind": "fixture",
                    "message": "bad\udc90 diagnostic",
                    "nested": [{"key\ud800": "Mắt em buồn"}],
                }
            )
        encoded = output.getvalue().encode("utf-8", errors="strict")
        payload = json.loads(encoded.decode("utf-8"))
        self.assertEqual(payload["message"], "bad\ufffd diagnostic")
        self.assertEqual(payload["nested"][0]["key\ufffd"], "Mắt em buồn")

    def test_worker_preserves_surrogate_path_internally_but_sanitizes_output(self) -> None:
        source = "C:\\media\\song\udc90.mp4"
        request = {
            "requestId": "request-id",
            "mediaPath": source,
            "lyricsPath": "C:\\data\\lyrics.txt",
            "title": "Song",
            "artist": None,
            "composer": None,
        }
        observed: dict[str, str] = {}

        def execute(args, _config, **callbacks):
            observed["source"] = args.source
            callbacks["on_output"](
                {"stream": "stderr", "stage": "probe", "text": "bad\udc90 output"}
            )
            return {"jobId": "job-id", "status": "succeeded", "artifacts": []}

        worker_input = io.StringIO(json.dumps(request, ensure_ascii=True) + "\n")
        worker_output = io.StringIO()
        with (
            patch.object(cli.sys, "stdin", worker_input),
            patch.object(cli.sys, "stdout", worker_output),
            patch.object(cli, "validate_project", return_value={"valid": True}),
            patch.object(cli, "load_project_config", return_value={"pipeline": {}}),
            patch.object(cli, "assert_model_provenance"),
            patch.object(cli, "_execute_job", side_effect=execute),
        ):
            self.assertEqual(cli._worker(Namespace(root=ROOT)), 0)
        self.assertEqual(observed["source"], source)
        encoded = worker_output.getvalue().encode("utf-8", errors="strict")
        payloads = [json.loads(line) for line in encoded.decode("utf-8").splitlines()]
        output = next(item for item in payloads if item["kind"] == "lyricrail.worker.output")
        self.assertEqual(output["outputText"], "bad\ufffd output")

    def test_worker_json_fallback_rejects_invalid_control_paths_and_numbers(self) -> None:
        output = io.StringIO()
        cycle: list[object] = []
        cycle.append(cycle)
        with patch.object(cli.sys, "stdout", output):
            cli._worker_emit(
                {
                    "kind": "lyricrail.worker.progress",
                    "requestId": "request-id",
                    "progressPercent": float("nan"),
                    "diagnostic": {"huge": 10**5_000, "cycle": cycle},
                }
            )
            cli._worker_emit(
                {
                    "kind": "lyricrail.worker.completed",
                    "requestId": "request-id",
                    "packagePath": "C:\\output\\bad\udc90.lrail",
                }
            )
            cli._worker_emit(
                {
                    "kind": "lyricrail.worker.completed",
                    "requestId": "request-id",
                    "packagePath": "C:\\output\\Song\ufffd - 😀 Singer [Karaoke].lrail",
                }
            )
        lines = output.getvalue().encode("utf-8", errors="strict").decode("utf-8").splitlines()
        progress = json.loads(lines[0], parse_constant=lambda value: self.fail(value))
        failed = json.loads(lines[1], parse_constant=lambda value: self.fail(value))
        completed = json.loads(lines[2], parse_constant=lambda value: self.fail(value))
        self.assertEqual(progress["progressPercent"], "<non-finite number>")
        self.assertEqual(progress["diagnostic"]["huge"], "<integer out of range>")
        self.assertEqual(failed["kind"], "lyricrail.worker.failed")
        self.assertNotIn("packagePath", failed)
        self.assertEqual(
            completed["packagePath"],
            "C:\\output\\Song\ufffd - 😀 Singer [Karaoke].lrail",
        )

    def test_subprocess_output_streams_before_exit_and_keeps_stdout_contract(self) -> None:
        context = CommandContext()
        completed = _run(
            context,  # type: ignore[arg-type]
            [
                sys.executable,
                "-c",
                (
                    "import sys,time; "
                    "print('first', flush=True); "
                    "print('warning', file=sys.stderr, flush=True); "
                    "time.sleep(0.6); print('last', flush=True)"
                ),
            ],
        )
        finished = time.monotonic()
        self.assertEqual(completed.stdout, "first\nlast")
        self.assertEqual(completed.stderr, "warning")
        streams = [stream for stream, _, _ in context.output]
        self.assertEqual(streams.count("stdout"), 2)
        self.assertEqual(streams.count("stderr"), 1)
        self.assertLess(min(timestamp for _, _, timestamp in context.output), finished - 0.25)
        self.assertTrue(context.logs[0].startswith("Executable: "))
        self.assertTrue(all("Command:" not in line for line in context.logs))

    def test_subprocess_cancellation_is_cooperative_and_bounded(self) -> None:
        context = CommandContext(cancel_after=0.2)
        started = time.monotonic()
        with self.assertRaisesRegex(RuntimeError, "controlled cancellation"):
            _run(
                context,  # type: ignore[arg-type]
                [sys.executable, "-c", "import time; print('started', flush=True); time.sleep(30)"],
            )
        self.assertLess(time.monotonic() - started, 5.0)

    def test_hostile_unterminated_line_and_ffmpeg_progress_are_bounded_and_truthful(self) -> None:
        context = CommandContext()
        _run(
            context,  # type: ignore[arg-type]
            [sys.executable, "-c", "import sys; sys.stdout.write('x' * 50000); sys.stdout.flush()"],
        )
        self.assertEqual(len(context.output), 1)
        self.assertLessEqual(len(context.output[0][1].encode("utf-8")), 16 * 1024 + 32)
        self.assertIn("<line truncated>", context.output[0][1])
        self.assertEqual(_command_duration_seconds(["ffmpeg", "-ss", "5", "-to", "15"]), 10.0)
        self.assertEqual(_command_duration_seconds(["ffmpeg", "-t", "00:01:30.5"]), 90.5)
        self.assertEqual(_ffmpeg_progress_seconds("out_time_us=2500000"), 2.5)
        self.assertEqual(_ffmpeg_progress_seconds("out_time=00:00:03.250"), 3.25)
        self.assertIsNone(_ffmpeg_progress_seconds("progress=continue"))

    def test_runner_output_callback_and_durable_logs_redact_and_rotate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            video = root / "song.mp4"
            video.write_bytes(b"media")
            store = JobStore(root / "output")
            job = store.create(
                video,
                PIPELINE,
                {"warnings": []},
                upload=False,
                lyrics_text="Exact lyric\n",
                lyrics_source_path=root / "lyrics.txt",
                lyrics_sha256="fixture",
                lyrics_line_count=1,
                lyrics_word_count=2,
            )
            output: list[dict[str, object]] = []

            def success(context: StageContext) -> list[dict[str, object]]:
                context.progress(50, "halfway")
                context.log(f"reading {Path.home() / 'private.mp4'} token=unsafe")
                return []

            final = PipelineRunner(
                store,
                handlers={stage.key: success for stage in STAGE_SPECS},
                on_output=output.append,
            ).run(job["jobId"])
            self.assertEqual(final["status"], "succeeded")
            self.assertTrue(any(event.get("stream") == "progress" for event in output))
            combined = "\n".join(str(event.get("text", "")) for event in output)
            self.assertNotIn("private.mp4", combined)
            self.assertNotIn("token=unsafe", combined)
            self.assertIn("<local path>", combined)

            store.log(
                job["jobId"],
                'api_key=TOPSECRET access_token=BEARER client_secret="HUSH" x-goog-signature=GOOG x-amz-signature=AMZ signature=RAW',
                stage="probe",
            )
            store.log(
                job["jobId"],
                r"failed C:\Music Library\Private Song.mp4 trailing-name.mp4",
                stage="probe",
            )
            durable = store.logs_path(job["jobId"], "probe").read_text(encoding="utf-8")
            self.assertNotIn("TOPSECRET", durable)
            self.assertNotIn("BEARER", durable)
            self.assertNotIn("HUSH", durable)
            self.assertNotIn("GOOG", durable)
            self.assertNotIn("AMZ", durable)
            self.assertNotIn("RAW", durable)
            self.assertNotIn("Private Song.mp4", durable)
            self.assertNotIn("trailing-name.mp4", durable)
            self.assertIn("<redacted>", durable)

            with (
                patch("lyricrail.job.MAX_PIPELINE_LOG_BYTES", 4096),
                patch("lyricrail.job.MAX_STAGE_LOG_BYTES", 2048),
            ):
                for index in range(200):
                    store.log(job["jobId"], f"line-{index} " + "z" * 100, stage="probe")
            stage_log = store.logs_path(job["jobId"], "probe")
            self.assertLessEqual(stage_log.stat().st_size, 2048)
            self.assertIn("older bounded task output removed", stage_log.read_text(encoding="utf-8"))

    def test_redaction_removes_remote_queries_private_paths_and_secrets(self) -> None:
        path = redact_diagnostic_text(
            r"failed C:\Music Library\Private Song.mp4 trailing-name.mp4"
        )
        value = redact_diagnostic_text(
            'https://host/video?token=x api_key=TOPSECRET access_token=BEARER client_secret="HUSH" x-goog-signature=GOOG x-amz-signature=AMZ signature=RAW Authorization: Bearer credential-value'
        )
        self.assertNotIn("Private Song.mp4", path)
        self.assertNotIn("trailing-name.mp4", path)
        self.assertNotIn("host/video", value)
        self.assertNotIn("TOPSECRET", value)
        self.assertNotIn("BEARER", value)
        self.assertNotIn("HUSH", value)
        self.assertNotIn("GOOG", value)
        self.assertNotIn("AMZ", value)
        self.assertNotIn("RAW", value)
        self.assertNotIn("credential-value", value)
        self.assertIn("<local path>", path)
        self.assertIn("<remote address>", value)
        self.assertIn("<redacted>", value)
        self.assertEqual(
            redact_diagnostic_text(r"Argument: C:\Music Library\Private Song.mp4"),
            "Argument: <local path>",
        )


def test_native_task_contract_is_single_sequenced_bounded_and_batched() -> None:
    tasks = (ROOT / "apps/player/src-tauri/src/tasks.rs").read_text(encoding="utf-8")
    for token in (
        'pub const TASKS_EVENT: &str = "task-runtime-update"',
        "const MAX_TASK_HISTORY: usize = 100",
        "const MAX_ACTIVE_TASKS: usize = 100_256",
        "const MAX_OUTPUT_LINES: usize = 1_000",
        "const MAX_OUTPUT_BYTES: usize = 1024 * 1024",
        "const MAX_PENDING_BYTES: usize = 256 * 1024",
        "Duration::from_millis(100)",
        "inner.sequence = inner.sequence.saturating_add(1)",
        "redact_diagnostic_text(text)",
        "ETA_MIN_SPAN",
        "ETA_MIN_PROGRESS",
        "pub fn restore_output",
    ):
        assert token in tasks
    assert "shell" not in tasks.lower()


def test_processing_and_every_long_domain_adapt_the_shared_task_registry() -> None:
    native_root = ROOT / "apps/player/src-tauri/src"
    processing = (native_root / "processing.rs").read_text(encoding="utf-8")
    model = (native_root / "model_installer.rs").read_text(encoding="utf-8")
    clip = (native_root / "local_clip.rs").read_text(encoding="utf-8")
    player = (native_root / "lib.rs").read_text(encoding="utf-8")
    cache = (native_root / "range_cache.rs").read_text(encoding="utf-8")
    worker = (ROOT / "src/lyricrail/__main__.py").read_text(encoding="utf-8")
    for token in (
        "id: request_id.clone()",
        "TaskKind::Processing",
        '"lyricrail.worker.output"',
        "stage_progress_percent",
        "restore_durable_tasks",
        "load_durable_manifest",
        "ProcessingTaskEvidence",
        "set_processing_task_evidence",
        "processing_failure_issue",
    ):
        assert token in processing
    assert '"kind": "lyricrail.worker.output"' in worker
    assert '"stageProgressPercent"' in worker
    assert "TaskKind::ModelInstall" in model and "tasks::append_command" in model
    assert "TaskKind::ClipPreparation" in clip and "tasks::append_command" in clip
    assert "TaskKind::LocalScan" in player
    assert "TaskKind::DriveScan" in player
    assert "TaskKind::DriveDownload" in player
    assert "download_in_background_with_progress" in cache


def test_processing_worker_has_one_utf8_launch_and_disconnect_failure_contract() -> None:
    processing = (ROOT / "apps/player/src-tauri/src/processing.rs").read_text(encoding="utf-8")
    for token in (
        '.arg("-X")',
        '.arg("utf8")',
        '.env("PYTHONIOENCODING", "utf-8:strict")',
        "handle_worker_disconnect(&disconnect_app, generation)",
        "handle_event(&stdout_app, event, generation)",
        "with_current_worker(app, generation",
        "recover_worker_disconnect(",
        "apply_current_generation(generation, current_generation",
        "dispatch_next(app, inner)",
        "Processing worker exited unexpectedly before reporting completion",
    ):
        assert token in processing


def test_retry_preserves_the_authenticated_job_instead_of_editing_lyrics() -> None:
    player = (ROOT / "apps/player/src-tauri/src/lib.rs").read_text(encoding="utf-8")
    retry = player.split("fn retry_processing_item", 1)[1].split(
        "fn cancel_processing_item", 1
    )[0]
    assert "bind_retry_lyrics_path(item, lyrics_path)" in retry
    assert "catalog.provide_lyrics" not in retry
    assert "enqueue_item(&app, queued, transient)" in retry


def test_activity_is_the_only_detailed_task_output_home() -> None:
    app = (ROOT / "apps/player/src/App.tsx").read_text(encoding="utf-8")
    css = (ROOT / "apps/player/src/App.css").read_text(encoding="utf-8")
    for token in (
        'listen<TaskRuntimeUpdate>("task-runtime-update"',
        'invoke<TaskSnapshot>("task_runtime_snapshot")',
        'invoke<TaskOutputSnapshot>("task_output_snapshot"',
        'invoke("cancel_task"',
        '"Pause view"',
        "Auto-scroll</label>",
        '"progress", "stdout", "stderr", "system"',
        "visibleTasks(taskState.tasks, nowMillis)",
        "onShowContext={showItemContext}",
        "onOpenIssueTask={(issue)",
    ):
        assert token in app
    for selector in (
        ".activity-drawer",
        ".activity-tabs",
        ".task-indeterminate",
        ".task-output-viewport",
        ".task-output-line",
    ):
        assert selector in css
    assert "visibleRange(filtered.length" in app


def test_scan_progress_precedes_expensive_work_and_rows_project_shared_tasks() -> None:
    player = (ROOT / "apps/player/src-tauri/src/lib.rs").read_text(encoding="utf-8")
    processing = (ROOT / "apps/player/src-tauri/src/processing.rs").read_text(encoding="utf-8")
    app = (ROOT / "apps/player/src/App.tsx").read_text(encoding="utf-8")
    local_scan = player.index('stage_title: Some("Scan selected local files"')
    local_work = player.index("spawn_blocking(move || scan_files(paths))")
    folder_scan = player.index('stage_title: Some("Scan local folder"')
    folder_work = player.index("spawn_blocking(move || scan_root(&path))")
    assert local_scan < local_work
    assert folder_scan < folder_work
    assert "let completed = index + 1" in player
    assert 'completed_units: Some(completed as u64)' in player
    connect = player.split("async fn connect_google_drive", 1)[1].split("async fn rescan_google_drive", 1)[0]
    assert connect.index('message: Some(format!("Scanning Drive root') < connect.index("expand_drive_root(&provider")
    assert 'listen<ProgressUpdate>("library-item-progress"' not in app
    assert 'className={`row-progress ${taskProgress' in app
    assert "onCancel={(item)" not in app
    assert 'invoke("cancel_task"' in app
    assert 'invoke<TaskRecord | null>("task_record"' in app
    assert '["queued", "processing", "failed", "setup-required"].includes(item.status)' in app
    assert "fn task_record(" in player
    assert '"library-item-progress"' not in processing


def test_large_queue_selection_is_bounded_without_append_only_order_tombstones() -> None:
    tasks = (ROOT / "apps/player/src-tauri/src/tasks.rs").read_text(encoding="utf-8")
    assert "BinaryHeap::<Reverse" in tasks
    assert "select_visible_tasks" in tasks
    assert "MAX_VISIBLE_TASKS" in tasks
    assert "\n    order: VecDeque<String>" not in tasks
    assert "inner.order" not in tasks
    assert "pub fn task(app: &AppHandle" in tasks


def test_model_install_activity_is_null_safe_and_root_render_failures_are_contained() -> None:
    app = (ROOT / "apps/player/src/App.tsx").read_text(encoding="utf-8")
    task_client = (ROOT / "apps/player/src/tasks.ts").read_text(encoding="utf-8")
    installer = (ROOT / "apps/player/src-tauri/src/model_installer.rs").read_text(encoding="utf-8")
    main = (ROOT / "apps/player/src/main.tsx").read_text(encoding="utf-8")
    boundary = (ROOT / "apps/player/src/AppErrorBoundary.tsx").read_text(encoding="utf-8")
    assert "task.stageProgressPercent != null" in app
    assert "task.progressPercent != null" in app
    assert "task.completedUnits != null && task.totalUnits != null" in app
    assert "task.etaSeconds != null" in app
    assert "normalizeTaskRecord" in task_client
    assert 'progress_mode: ProgressMode::Indeterminate' in installer
    assert "<AppErrorBoundary>" in main
    assert "Native background work" in boundary
    assert "Reload interface" in boundary


def test_model_install_progress_prefers_useful_transfer_data_without_losing_raw_output() -> None:
    app = (ROOT / "apps/player/src/App.tsx").read_text(encoding="utf-8")
    presenter = (ROOT / "apps/player/src/modelProgress.ts").read_text(encoding="utf-8")
    assert "latestModelTransferProgress" in app
    assert 'task.kind !== "model-install" && task.etaSeconds != null' in app
    assert "Current download" in app
    assert "Setup progress" in app
    assert '>Raw</button>' in app
    assert 'rawOutput ? line.text : friendlyOutputText(line)' in app
    assert 'line.taskId === "model-install"' in app
    assert "TRANSFER_LINE" in presenter
    assert "compactFriendlyOutput" in presenter
    assert '"download-and-verify": "model setup"' in presenter


if __name__ == "__main__":
    unittest.main()
