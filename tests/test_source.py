from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from lyricrail.source import (
    _source_download_template,
    is_http_url,
    media_kind_from_path,
    parse_timecode,
    resolve_source,
)


class SourceTests(unittest.TestCase):
    def test_full_download_fallback_uses_a_distinct_cache_folder(self) -> None:
        cache_root = Path("cache") / "sources"
        ranged = _source_download_template(cache_root, "-range-185.000-394.000")
        full = _source_download_template(cache_root, "")
        self.assertNotEqual(ranged, full)
        self.assertIn("-range-185.000-394.000", ranged)
        self.assertNotIn("-range-", full)

    def test_accepts_local_audio_and_video(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            audio = root / "Song - Artist.flac"
            video = root / "Song - Artist.mp4"
            audio.write_bytes(b"audio")
            video.write_bytes(b"video")
            self.assertEqual(resolve_source(root, str(audio)).media_kind_hint, "audio")
            self.assertEqual(resolve_source(root, str(video)).media_kind_hint, "video")

    def test_url_and_extension_detection(self) -> None:
        self.assertTrue(is_http_url("https://youtu.be/example"))
        self.assertFalse(is_http_url(r"E:\\Music\\song.mp3"))
        self.assertEqual(media_kind_from_path(Path("song.m4a")), "audio")
        with self.assertRaisesRegex(ValueError, "Unsupported media format"):
            media_kind_from_path(Path("notes.txt"))

    def test_timecodes_and_local_range(self) -> None:
        self.assertEqual(parse_timecode("185"), 185.0)
        self.assertEqual(parse_timecode("03:05"), 185.0)
        self.assertEqual(parse_timecode("1:02:03.5"), 3723.5)
        with self.assertRaisesRegex(ValueError, "later"):
            with tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                media = root / "song.mp3"
                media.write_bytes(b"audio")
                resolve_source(root, str(media), start_seconds=20, end_seconds=10)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            media = root / "compilation.mp4"
            media.write_bytes(b"video")
            source = resolve_source(
                root,
                str(media),
                start_seconds=parse_timecode("03:05"),
                end_seconds=parse_timecode("06:32"),
                title_override="Tôi Vẫn Nhớ",
                artist_override="Băng Tâm & Đan Nguyên",
            )
            self.assertEqual(source.media_trim_start_seconds, 185.0)
            self.assertEqual(source.media_trim_end_seconds, 392.0)
            self.assertEqual(source.song_title, "Tôi Vẫn Nhớ")

    def test_implicit_input_must_be_unambiguous(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "input").mkdir()
            (root / "input" / "a.mp3").write_bytes(b"a")
            (root / "input" / "b.mp4").write_bytes(b"b")
            with self.assertRaisesRegex(ValueError, "exactly one"):
                resolve_source(root, None)


if __name__ == "__main__":
    unittest.main()
