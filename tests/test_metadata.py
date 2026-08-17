from pathlib import Path
import unittest

from lyricrail.metadata import build_youtube_metadata, parse_video_identity


class MetadataTests(unittest.TestCase):
    def test_filename_identity(self) -> None:
        song, artist = parse_video_identity(Path("Xin Làm Người Xa Lạ - Đan Nguyên.mp4"))
        self.assertEqual(song, "Xin Làm Người Xa Lạ")
        self.assertEqual(artist, "Đan Nguyên")

    def test_build_metadata(self) -> None:
        channel = {
            "channelDisplayName": "Kênh thử nghiệm",
            "defaultPlaylistId": "PL_TEST",
            "rightsNotice": "Nội dung được sử dụng hợp lệ.",
            "descriptionFooter": "Theo dõi kênh.",
            "defaultPrivacy": "private",
            "madeForKids": False,
        }
        rules = {
            "titleTemplate": "Karaoke {songTitle} - {artist} | Beat Chuẩn",
            "titleTemplateWithoutArtist": "Karaoke {songTitle} | Beat Chuẩn",
            "descriptionTemplate": "{songTitle}\n{artist}\n{rightsNotice}\n{descriptionFooter}",
            "descriptionTemplateWithoutArtist": "{songTitle}\n{rightsNotice}",
            "tags": ["karaoke", "{songTitle}", "{artist}"],
            "categoryId": "10",
            "defaultLanguage": "vi",
            "captionLanguage": "vi",
            "captionName": "Tiếng Việt",
            "limits": {
                "titleCharacters": 100,
                "descriptionCharacters": 5000,
                "tagsCharacters": 500,
            },
        }

        metadata = build_youtube_metadata(
            Path("Xin Làm Người Xa Lạ - Đan Nguyên.mp4"), channel, rules
        )
        snippet = metadata["insertBody"]["snippet"]
        self.assertEqual(snippet["categoryId"], "10")
        self.assertEqual(snippet["defaultLanguage"], "vi")
        self.assertEqual(snippet["defaultAudioLanguage"], "vi")
        self.assertIn("Đan Nguyên", snippet["title"])
        self.assertEqual(
            metadata["insertBody"]["status"]["privacyStatus"], "private"
        )

    def test_command_identity_overrides_compilation_filename(self) -> None:
        channel = {"defaultPrivacy": "private"}
        rules = {
            "titleTemplate": "{songTitle} - {artist}",
            "titleTemplateWithoutArtist": "{songTitle}",
            "descriptionTemplate": "{songTitle} {artist}",
            "descriptionTemplateWithoutArtist": "{songTitle}",
            "tags": ["{songTitle}", "{artist}"],
            "categoryId": "10",
        }
        metadata = build_youtube_metadata(
            Path("Two hour compilation.mp4"),
            channel,
            rules,
            song_title="Tôi Vẫn Nhớ",
            artist="Băng Tâm & Đan Nguyên",
        )
        self.assertEqual(metadata["source"]["identityMethod"], "command")
        self.assertEqual(
            metadata["insertBody"]["snippet"]["title"],
            "Tôi Vẫn Nhớ - Băng Tâm & Đan Nguyên",
        )


if __name__ == "__main__":
    unittest.main()
