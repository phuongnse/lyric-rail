import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from lyricrail.__main__ import build_parser
from lyricrail.job import JobStore
from lyricrail.local_pipeline import _load_lyrics, build_local_handlers
from lyricrail.lyric_input import (
    load_authoritative_lyrics,
    normalize_authoritative_lyrics,
)
from lyricrail.runner import StageContext


class LyricInputTests(unittest.TestCase):
    def test_validation_preserves_the_exact_authoritative_string(self) -> None:
        supplied = "  Mắt em buồn!  \r\n\r\nNhưng đêm nay gọi tên anh…  "
        text, lines = normalize_authoritative_lyrics(supplied)
        self.assertEqual(lines, ("  Mắt em buồn!  ", "Nhưng đêm nay gọi tên anh…  "))
        self.assertEqual(text, supplied)

    def test_utf8_file_is_hashed_without_normalizing_user_text(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "lyrics.txt"
            path.write_bytes("Câu một\nCâu hai\n".encode("utf-8"))
            first = load_authoritative_lyrics(path)
            second = load_authoritative_lyrics(path)
            self.assertEqual(first.sha256, second.sha256)
            self.assertEqual(
                first.sha256,
                hashlib.sha256("Câu một\nCâu hai\n".encode("utf-8")).hexdigest(),
            )
            self.assertEqual(first.word_count, 4)

    def test_empty_lyrics_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "no lyric lines"):
            normalize_authoritative_lyrics("\n  \n")

    def test_plan_and_run_require_a_lyrics_file_argument(self) -> None:
        parser = build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(["run", "song.mp4"])
        args = parser.parse_args(["run", "song.mp4", "--lyrics", "lyrics.txt"])
        self.assertEqual(args.lyrics, Path("lyrics.txt"))

    def test_load_stage_preserves_exact_text_and_verifies_snapshot(self) -> None:
        normalized, lines = normalize_authoritative_lyrics(
            "Mắt em buồn!\nNhưng đêm nay gọi tên anh…\n"
        )
        digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "song.mp4"
            source.write_bytes(b"media")
            lyrics = root / "lyrics.txt"
            lyrics.write_text(normalized, encoding="utf-8")
            store = JobStore(root / "output")
            job = store.create(
                source,
                {"pipelineVersion": 1, "quality": {"mode": "maximum"}},
                {},
                upload=False,
                lyrics_text=normalized,
                lyrics_source_path=lyrics,
                lyrics_sha256=digest,
                lyrics_line_count=len(lines),
                lyrics_word_count=sum(len(line.split()) for line in lines),
            )
            store.update_job(
                job["jobId"], status="running", startedAt=job["createdAt"]
            )
            context = StageContext(
                store=store,
                job_id=job["jobId"],
                stage_key="load_lyrics",
                job_directory=Path(job["paths"]["jobDirectory"]),
            )

            _load_lyrics(context)

            payload_path = context.job_directory / "work" / "shared" / "authoritative-lyrics.json"
            payload_bytes = payload_path.read_bytes()
            payload = json.loads(payload_bytes.decode("utf-8", errors="strict"))
            self.assertEqual([item["text"] for item in payload["lines"]], list(lines))
            self.assertFalse(payload["detectedTextUsed"])
            self.assertFalse(payload["captionUsed"])
            self.assertEqual(
                (context.job_directory / "inputs" / "lyrics.txt").read_bytes(),
                normalized.encode("utf-8"),
            )
            self.assertEqual(payload["sha256"], digest)
            self.assertEqual(build_local_handlers(root)["load_lyrics"], _load_lyrics)


if __name__ == "__main__":
    unittest.main()
