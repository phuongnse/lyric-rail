from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from lyricrail.job import JobStore, STAGE_SPECS
from lyricrail.runner import PipelineRunner, StageContext


PIPELINE = {
    "pipelineVersion": 1,
    "quality": {"mode": "maximum"},
    "youtube": {},
}


class RunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.video = self.root / "Song - Artist.mp4"
        self.video.write_bytes(b"test")
        self.store = JobStore(self.root / "output")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def create_job(self) -> dict:
        return self.store.create(
            self.video,
            PIPELINE,
            {"warnings": []},
            upload=False,
            lyrics_text="Exact lyric text\n",
            lyrics_source_path=self.root / "lyrics.txt",
            lyrics_sha256="test-hash",
            lyrics_line_count=1,
            lyrics_word_count=3,
        )

    def test_missing_handler_blocks_job_with_machine_readable_error(self) -> None:
        job = self.create_job()
        final = PipelineRunner(self.store).run(job["jobId"])
        self.assertEqual(final["status"], "blocked")
        self.assertEqual(final["error"]["code"], "STAGE_HANDLER_MISSING")
        self.assertEqual(final["error"]["stage"], "probe")

    def test_successful_handlers_complete_active_pipeline(self) -> None:
        def success(context: StageContext):
            context.progress(50, "halfway")
            return []

        handlers = {stage.key: success for stage in STAGE_SPECS}
        job = self.create_job()
        final = PipelineRunner(self.store, handlers=handlers).run(job["jobId"])
        self.assertEqual(final["status"], "succeeded")
        self.assertEqual(final["progressPercent"], 100.0)
        self.assertTrue(
            all(
                stage["status"] in {"succeeded", "skipped"}
                for stage in final["stages"]
            )
        )

    def test_handler_exception_fails_job_without_losing_logs(self) -> None:
        def failure(context: StageContext):
            raise RuntimeError("model crashed")

        job = self.create_job()
        final = PipelineRunner(self.store, handlers={"probe": failure}).run(job["jobId"])
        self.assertEqual(final["status"], "failed")
        self.assertEqual(final["error"]["code"], "STAGE_EXECUTION_FAILED")
        log = self.store.logs_path(job["jobId"], "probe").read_text(encoding="utf-8")
        self.assertIn("RuntimeError", log)

    def test_retry_preserves_successful_stages_before_failure(self) -> None:
        def success(context: StageContext):
            return []

        def failure(context: StageContext):
            raise RuntimeError("separation failed")

        job = self.create_job()
        first = PipelineRunner(
            self.store,
            handlers={
                "probe": success,
                "extract_audio": success,
                "separate_stems": failure,
            },
        ).run(job["jobId"])
        self.assertEqual(first["status"], "failed")
        retried = self.store.prepare_retry(job["jobId"])
        self.assertEqual(retried["stages"][0]["status"], "succeeded")
        self.assertEqual(retried["stages"][1]["status"], "succeeded")
        self.assertEqual(retried["stages"][2]["status"], "pending")


if __name__ == "__main__":
    unittest.main()
