from __future__ import annotations

import argparse
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from lyricrail.__main__ import _execute_job
from lyricrail.job import JobStore, STAGE_SPECS
from lyricrail.lyric_input import load_authoritative_lyrics
from lyricrail.runner import PipelineRunner, StageContext, merge_artifacts
from lyricrail.source import ResolvedSource


PIPELINE = {
    "pipelineVersion": 1,
    "quality": {"mode": "maximum"},
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

    def test_player_resume_recovers_an_interrupted_running_manifest(self) -> None:
        lyrics_path = self.root / "lyrics.txt"
        lyrics_path.write_text("Exact lyric text\n", encoding="utf-8")
        lyrics = load_authoritative_lyrics(lyrics_path)
        job = self.store.create(
            self.video,
            PIPELINE,
            {"warnings": []},
            upload=False,
            lyrics_text=lyrics.text,
            lyrics_source_path=lyrics.source_path,
            lyrics_sha256=lyrics.sha256,
            lyrics_line_count=len(lyrics.lines),
            lyrics_word_count=lyrics.word_count,
        )
        self.store.update_job(
            job["jobId"], status="running", startedAt=job["createdAt"]
        )
        self.store.update_stage(job["jobId"], "probe", status="running")
        self.store.update_stage(job["jobId"], "probe", status="succeeded")
        self.store.update_stage(job["jobId"], "extract_audio", status="running")

        def success(_context: StageContext):
            return []

        source = ResolvedSource(
            input_value=str(self.video),
            path=self.video.resolve(),
            origin="local",
            media_kind_hint="video",
        )
        with (
            patch("lyricrail.__main__._root", return_value=self.root),
            patch("lyricrail.__main__._resolved_source", return_value=source),
            patch("lyricrail.__main__._resolved_lyrics", return_value=lyrics),
            patch(
                "lyricrail.__main__.resolve_data_root",
                return_value=self.store.output_root.parent,
            ),
            patch(
                "lyricrail.__main__.build_local_handlers",
                return_value={stage.key: success for stage in STAGE_SPECS},
            ),
        ):
            final = _execute_job(
                argparse.Namespace(),
                {"pipeline": PIPELINE},
                verify_models=False,
                resume_job_id=job["jobId"],
            )

        self.assertEqual(final["jobId"], job["jobId"])
        self.assertEqual(final["status"], "succeeded")
        self.assertEqual(final["stages"][0]["attempts"], 1)
        self.assertEqual(final["stages"][1]["attempts"], 2)

    def test_package_artifact_update_boundary_is_idempotent_on_retry(self) -> None:
        job = self.create_job()
        self.store.update_job(
            job["jobId"], status="running", startedAt=job["createdAt"]
        )
        current = self.store.load(job["jobId"])
        for stage in current["stages"]:
            if stage["key"] == "package_lrail":
                break
            if stage["status"] == "skipped":
                continue
            self.store.update_stage(job["jobId"], stage["key"], status="running")
            self.store.update_stage(job["jobId"], stage["key"], status="succeeded")
        self.store.update_stage(job["jobId"], "package_lrail", status="running")
        package = self.root / "output" / "durable.lrail"
        package.write_bytes(b"verified package fixture")
        artifact = {
            "kind": "lrail-package",
            "label": "Authenticated LyricRail karaoke package",
            "path": str(package.resolve()),
            "sizeBytes": package.stat().st_size,
        }
        self.store.update_job(job["jobId"], artifacts=[artifact])

        with self.store.run_lease(job["jobId"]):
            self.store.prepare_retry(job["jobId"], allow_interrupted=True)

        final = PipelineRunner(
            self.store,
            handlers={"package_lrail": lambda _context: [artifact]},
        ).run(job["jobId"])
        packages = [
            item for item in final["artifacts"] if item["kind"] == "lrail-package"
        ]
        self.assertEqual(final["status"], "succeeded")
        self.assertEqual(packages, [artifact])
        package_stage = next(
            stage for stage in final["stages"] if stage["key"] == "package_lrail"
        )
        self.assertEqual(package_stage["attempts"], 2)

    def test_artifact_merge_replaces_the_same_kind_and_path(self) -> None:
        old = {"kind": "lrail-package", "path": "/output/song.lrail", "sizeBytes": 1}
        current = {**old, "sizeBytes": 2}
        self.assertEqual(merge_artifacts([old, old], [current]), [current])


if __name__ == "__main__":
    unittest.main()
