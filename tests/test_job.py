from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from lyricrail.job import JobStore


PIPELINE = {
    "pipelineVersion": 1,
    "quality": {"mode": "maximum"},
    "package": {"enabled": True, "cleanupVerifiedIntermediates": True},
    "youtube": {
        "uploadThumbnail": True,
        "addToPlaylist": True,
        "waitForProcessing": True,
        "publishWhenReady": False,
    },
}


class JobStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.video = self.root / "Bài Hát - Ca Sĩ.mp4"
        self.video.write_bytes(b"test")
        self.store = JobStore(self.root / "output")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def create_job(self, upload: bool = False) -> dict:
        return self.store.create(
            self.video,
            PIPELINE,
            {"warnings": ["test warning"]},
            upload=upload,
            lyrics_text="Lời ca chính xác\n",
            lyrics_source_path=self.root / "lyrics.txt",
            lyrics_sha256="test-hash",
            lyrics_line_count=1,
            lyrics_word_count=4,
        )

    def test_create_has_atomic_manifest_and_event_journal(self) -> None:
        job = self.create_job()
        directory = Path(job["paths"]["jobDirectory"])
        self.assertEqual(job["status"], "queued")
        self.assertTrue((directory / "job.json").is_file())
        self.assertTrue((directory / "events.jsonl").is_file())
        self.assertEqual(
            (directory / "inputs" / "lyrics.txt").read_text(encoding="utf-8"),
            "Lời ca chính xác\n",
        )
        self.assertFalse(job["request"]["lyrics"]["detectedTextUsed"])
        self.assertEqual(job["runtime"]["projectRoot"], str(self.root.resolve()))
        event = json.loads((directory / "events.jsonl").read_text().splitlines()[0])
        self.assertEqual(event["type"], "job.created")
        self.assertEqual(len(job["stages"]), 17)
        statuses = {stage["key"]: stage["status"] for stage in job["stages"]}
        self.assertEqual(statuses["create_thumbnail"], "skipped")
        self.assertEqual(statuses["render_player_media"], "pending")
        self.assertEqual(statuses["package_lrail"], "pending")
        self.assertEqual(statuses["cleanup_intermediates"], "pending")
        self.assertEqual(statuses["render_master"], "skipped")

    def test_each_created_job_has_an_isolated_empty_workspace(self) -> None:
        first = self.create_job()
        first_directory = Path(first["paths"]["jobDirectory"])
        stale = first_directory / "work" / "shared" / "aligned-lyrics.json"
        stale.parent.mkdir(parents=True)
        stale.write_text('{"from":"first-job"}', encoding="utf-8")

        second = self.create_job()
        second_directory = Path(second["paths"]["jobDirectory"])

        self.assertNotEqual(first["jobId"], second["jobId"])
        self.assertNotEqual(first_directory, second_directory)
        self.assertFalse((second_directory / "work" / "shared").exists())
        self.assertEqual(
            second["execution"],
            {
                "isolationPolicy": "fresh-job-no-intermediate-reuse",
                "crossJobIntermediateReuse": False,
                "sharedCaches": ["source-media", "model-weights"],
            },
        )
        self.assertTrue(stale.is_file())

    def test_stage_progress_updates_weighted_job_progress(self) -> None:
        job = self.create_job()
        job = self.store.update_job(job["jobId"], status="running", startedAt=job["createdAt"])
        job = self.store.update_stage(
            job["jobId"], "probe", status="running", progress_percent=50
        )
        self.assertGreater(job["progressPercent"], 0)
        job = self.store.update_stage(job["jobId"], "probe", status="succeeded")
        self.assertGreater(job["progressPercent"], 2)

    def test_invalid_state_transition_is_rejected(self) -> None:
        job = self.create_job()
        with self.assertRaisesRegex(ValueError, "transition"):
            self.store.update_job(job["jobId"], status="succeeded")

    def test_latest_resolves_most_recent_job(self) -> None:
        job = self.create_job()
        self.assertEqual(self.store.load("latest")["jobId"], job["jobId"])

    def test_queued_cancel_is_immediate_and_retryable(self) -> None:
        job = self.create_job()
        cancelled = self.store.request_cancel(job["jobId"])
        self.assertEqual(cancelled["status"], "cancelled")
        retried = self.store.prepare_retry(job["jobId"])
        self.assertEqual(retried["status"], "queued")
        self.assertEqual(retried["retryCount"], 1)


if __name__ == "__main__":
    unittest.main()
