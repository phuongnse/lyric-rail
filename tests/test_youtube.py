from __future__ import annotations

import unittest

from lyricrail.youtube import execute_resumable, verify_channel


class _Channels:
    def __init__(self, response: dict) -> None:
        self.response = response

    def list(self, **_kwargs: object) -> "_Channels":
        return self

    def execute(self) -> dict:
        return self.response


class _Service:
    def __init__(self, response: dict) -> None:
        self.response = response

    def channels(self) -> _Channels:
        return _Channels(self.response)


class _Status:
    def progress(self) -> float:
        return 0.5


class _UploadRequest:
    def __init__(self) -> None:
        self.calls = 0

    def next_chunk(self) -> tuple[object, object]:
        self.calls += 1
        if self.calls == 1:
            return _Status(), None
        return None, {"id": "video123"}


class YouTubeTests(unittest.TestCase):
    def test_channel_guard(self) -> None:
        service = _Service({"items": [{"id": "UC_OK", "snippet": {"title": "Mine"}}]})
        self.assertEqual(verify_channel(service, "UC_OK")["channelTitle"], "Mine")
        with self.assertRaisesRegex(RuntimeError, "not the configured channel"):
            verify_channel(service, "UC_OTHER")

    def test_resumable_upload_reports_progress(self) -> None:
        progress: list[float] = []
        response = execute_resumable(_UploadRequest(), progress=progress.append)
        self.assertEqual(response["id"], "video123")
        self.assertEqual(progress, [50.0])


if __name__ == "__main__":
    unittest.main()
