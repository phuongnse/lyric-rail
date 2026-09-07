from pathlib import Path

import pytest

from lyricrail.__main__ import build_parser
from lyricrail.config import load_project_config
from lyricrail.job import build_stage_plan
from lyricrail.local_pipeline import apply_embedded_media_metadata
from lyricrail.metadata import build_local_metadata, parse_media_identity
from lyricrail.source import resolve_source


def test_local_source_contract_rejects_remote_processing(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="local disk media only"):
        resolve_source(tmp_path, "https://example.test/song.mp4")

    media = tmp_path / "Bài hát - Ca sĩ.mp4"
    media.write_bytes(b"media")
    resolved = resolve_source(
        tmp_path,
        str(media),
        composer_override="Nhạc sĩ",
    )
    assert resolved.path == media.resolve()
    assert resolved.origin == "local"
    assert resolved.composer == "Nhạc sĩ"


def test_local_metadata_is_conservative_and_searchable(tmp_path: Path) -> None:
    media = tmp_path / "Diễm xưa - Khánh Ly.mp4"
    title, artist = parse_media_identity(media)
    assert (title, artist) == ("Diễm xưa", "Khánh Ly")
    metadata = build_local_metadata(media, composer="Trịnh Công Sơn")
    assert metadata["source"] == {
        "mediaFile": str(media.resolve()),
        "identityMethod": "command",
        "songTitle": "Diễm xưa",
        "referenceArtist": "Khánh Ly",
        "composer": "Trịnh Công Sơn",
    }
    embedded = {"source": {"songTitle": "Explicit"}}
    assert apply_embedded_media_metadata(
        embedded,
        {
            "format": {
                "tags": {
                    "title": "Ignored",
                    "artist": "Khánh Ly",
                    "composer": "Trịnh Công Sơn",
                }
            }
        },
    )
    assert embedded["source"]["songTitle"] == "Explicit"
    assert embedded["source"]["referenceArtist"] == "Khánh Ly"
    assert embedded["source"]["composer"] == "Trịnh Công Sơn"


def test_cli_has_persistent_worker_and_no_upload_option() -> None:
    parser = build_parser()
    worker = parser.parse_args(["worker", "--root", "."])
    assert worker.command == "worker"
    run = parser.parse_args(
        ["run", "song.mp4", "--lyrics", "song.txt", "--composer", "Composer"]
    )
    assert run.composer == "Composer"
    with pytest.raises(SystemExit):
        parser.parse_args(["run", "song.mp4", "--lyrics", "song.txt", "--upload"])


def test_removed_delivery_configuration_cannot_reenable_cloud_processing() -> None:
    root = Path(__file__).resolve().parents[1]
    pipeline = load_project_config(root)["pipeline"]
    assert "youtube" not in pipeline
    assert "youtubeUpload" not in pipeline["quality"]
    assert "audioOnlyVisuals" not in pipeline
    with pytest.raises(ValueError, match="removed"):
        build_stage_plan(pipeline, upload=True)
