from __future__ import annotations

import unittest
from unittest.mock import patch

from lyricrail.visuals import infer_landscape_queries, mixkit_search, stock_video_from_page


class VisualSelectionTests(unittest.TestCase):
    def test_vietnamese_song_language_changes_queries(self) -> None:
        queries = infer_landscape_queries(
            "Một đêm mưa buồn", "Em nhớ biển và con đường về"
        )
        self.assertIn("moonlit mountains night sky", queries)
        self.assertIn("cinematic ocean waves dusk", queries)
        self.assertIn("country road through scenic fields", queries)

    @patch("lyricrail.visuals._fetch_text")
    def test_search_rejects_people_and_abstract_clips(self, fetch: object) -> None:
        fetch.return_value = """
          <a href='/free-stock-video/man-walking-by-a-mountain-10/'>bad person</a>
          <a href='/free-stock-video/black-ink-splashing-11/'>bad abstract</a>
          <a href='/free-stock-video/clouds-over-a-mountain-valley-12/'>good</a>
        """
        self.assertEqual(
            mixkit_search("mountains"),
            [("https://mixkit.co/free-stock-video/clouds-over-a-mountain-valley-12/", "clouds over a mountain valley")],
        )

    @patch("lyricrail.visuals._fetch_text")
    def test_prefers_1080_download(self, fetch: object) -> None:
        fetch.return_value = "https://assets.mixkit.co/videos/42/42-720.mp4"
        asset = stock_video_from_page(
            "https://mixkit.co/free-stock-video/mountain-view-42/", "mountain view", "mountain"
        )
        self.assertEqual(asset.download_url, "https://assets.mixkit.co/videos/42/42-1080.mp4")


if __name__ == "__main__":
    unittest.main()
