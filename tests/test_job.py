from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from lyricrail.job import (
    JobStore,
    append_json_line,
    atomic_write_json,
    replace_unpaired_surrogates,
    sanitize_diagnostic_payload,
)


PIPELINE = {
    "pipelineVersion": 1,
    "quality": {"mode": "maximum"},
    "package": {"enabled": True, "cleanupVerifiedIntermediates": True},
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
        self.assertEqual(len(job["stages"]), 12)
        statuses = {stage["key"]: stage["status"] for stage in job["stages"]}
        self.assertEqual(statuses["create_thumbnail"], "pending")
        self.assertEqual(statuses["render_player_media"], "pending")
        self.assertEqual(statuses["package_lrail"], "pending")
        self.assertEqual(statuses["cleanup_intermediates"], "pending")

    def test_internal_json_escapes_surrogates_and_roundtrips_exact_values(self) -> None:
        surrogate_path = "C:\\media\\part\udc90.mp4"
        exact_lyrics = "Mắt em buồn!\nNhưng đêm nay gọi tên anh…\n"
        document = {"path": surrogate_path, "lyrics": exact_lyrics}
        manifest = self.root / "internal.json"
        events = self.root / "internal.jsonl"
        atomic_write_json(manifest, document)
        append_json_line(events, document)
        manifest_bytes = manifest.read_bytes()
        event_bytes = events.read_bytes()
        manifest_bytes.decode("utf-8", errors="strict")
        event_bytes.decode("utf-8", errors="strict")
        self.assertIn(b"\\udc90", manifest_bytes.lower())
        self.assertEqual(json.loads(manifest.read_text(encoding="utf-8")), document)
        self.assertEqual(json.loads(events.read_text(encoding="utf-8")), document)
        self.assertEqual(json.loads(manifest.read_text(encoding="utf-8"))["lyrics"], exact_lyrics)

    def test_diagnostic_surrogates_are_replaced_and_bounded_before_logging(self) -> None:
        job = self.create_job()
        rendered = self.store.log(job["jobId"], "bad\udc90 diagnostic")
        self.assertEqual(rendered, "bad\ufffd diagnostic")
        log_path = self.store.logs_path(job["jobId"])
        self.assertIn("bad\ufffd diagnostic", log_path.read_text(encoding="utf-8"))
        nested = sanitize_diagnostic_payload(
            {"bad\udc90-key": ["value\udc90", {"nested": "Mắt em buồn"}]}
        )
        encoded = json.dumps(nested, ensure_ascii=False).encode("utf-8", errors="strict")
        self.assertIn("bad\ufffd-key", encoded.decode("utf-8"))
        self.assertEqual(nested["bad\ufffd-key"][1]["nested"], "Mắt em buồn")
        self.assertEqual(replace_unpaired_surrogates("\ud83d\ude00"), "😀")
        self.assertEqual(replace_unpaired_surrogates("x\ud800y"), "x\ufffdy")

    def test_diagnostic_payload_has_one_finite_global_json_budget(self) -> None:
        cycle: list[object] = []
        cycle.append(cycle)
        payload = {
            "nan": float("nan"),
            "infinity": float("inf"),
            "huge": 10**5_000,
            "wide": {f"key-{index}": index for index in range(10_000)},
            "cycle": cycle,
        }
        safe = sanitize_diagnostic_payload(payload)
        encoded = json.dumps(safe, ensure_ascii=True, allow_nan=False).encode("utf-8")
        self.assertLessEqual(len(encoded), 512 * 1024)
        self.assertEqual(safe["nan"], "<non-finite number>")
        self.assertEqual(safe["infinity"], "<non-finite number>")
        self.assertEqual(safe["huge"], "<integer out of range>")
        self.assertLessEqual(len(safe["wide"]), 257)
        self.assertEqual(safe["cycle"][0], "<cyclic diagnostic>")

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
                "isolationPolicy": "persistent-worker-fresh-job-artifacts",
                "crossJobIntermediateReuse": False,
                "sharedCaches": ["model-weights", "loaded-models"],
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

    def test_interrupted_running_job_retries_from_active_stage_and_keeps_successes(self) -> None:
        job = self.create_job()
        job = self.store.update_job(
            job["jobId"], status="running", startedAt=job["createdAt"]
        )
        self.store.update_stage(job["jobId"], "probe", status="running")
        self.store.update_stage(job["jobId"], "probe", status="succeeded")
        self.store.update_stage(job["jobId"], "extract_audio", status="running")

        with self.store.run_lease(job["jobId"]):
            retried = self.store.prepare_retry(
                job["jobId"], allow_interrupted=True
            )

        self.assertEqual(retried["status"], "queued")
        stages = {stage["key"]: stage for stage in retried["stages"]}
        self.assertEqual(stages["probe"]["status"], "succeeded")
        self.assertEqual(stages["extract_audio"]["status"], "pending")
        self.assertEqual(retried["retryCount"], 1)

    def test_run_lease_rejects_a_second_live_worker(self) -> None:
        job = self.create_job()
        with self.store.run_lease(job["jobId"]):
            with self.assertRaisesRegex(ValueError, "another processing worker"):
                with self.store.run_lease(job["jobId"]):
                    self.fail("a second worker acquired the same run lease")


if __name__ == "__main__":
    unittest.main()
