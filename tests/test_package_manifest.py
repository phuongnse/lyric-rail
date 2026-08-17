from pathlib import Path

from lyricrail.package_manifest import build_package_request, build_release_metadata


def test_release_metadata_keeps_sources_but_not_local_absolute_paths(tmp_path: Path) -> None:
    local_source = tmp_path / "Song - Artist.mp4"
    job = {
        "jobId": "job-1",
        "runtime": {"lyricRailVersion": "0.8.0"},
        "request": {
            "sourceOrigin": "local",
            "sourceMedia": str(local_source),
            "sourceRange": {"startSeconds": 1.0, "endSeconds": 2.0},
            "lyrics": {"sha256": "abc", "lineCount": 2, "wordCount": 5},
        },
    }
    delivery = {
        "source": {"songTitle": "Song", "referenceArtist": "Artist"},
        "insertBody": {
            "snippet": {
                "description": "Professional description",
                "tags": ["karaoke"],
                "defaultLanguage": "vi",
            }
        },
    }
    release = build_release_metadata(job, delivery, {}, {"quality": {}})
    assert release["title"] == "Song"
    assert release["sources"][0]["fileName"] == local_source.name
    assert str(tmp_path) not in str(release)
    assert release["rights"]["ownershipClaimed"] is False


def test_package_request_contains_dynamic_lyrics_and_dual_audio_metadata(
    tmp_path: Path,
) -> None:
    paths = []
    for name in (
        "video.mp4",
        "karaoke.m4a",
        "original.m4a",
        "timing.json",
        "plan.json",
        "release.json",
        "template.json",
    ):
        path = tmp_path / name
        path.write_bytes(b"x")
        paths.append(path)
    release = {"producer": {"name": "LyricRail Studio"}}
    request = build_package_request(
        release,
        playback_video=paths[0],
        karaoke_audio=paths[1],
        original_audio=paths[2],
        lyrics_timing=paths[3],
        render_plan=paths[4],
        release_metadata_file=paths[5],
        presentation_template=paths[6],
        minimum_player_version="0.8.0",
    )
    assert request["assets"][0]["logicalName"] == "media/video.mp4"
    assert request["assets"][2]["logicalName"] == "audio/original-reference.m4a"
    assert request["assets"][2]["mediaType"] == "audio/mp4"
    assert {asset["kind"] for asset in request["assets"]} >= {
        "playback-video",
        "playback-audio",
        "lyrics-timing",
        "lyrics-render-plan",
        "release-metadata",
    }


def test_package_request_preserves_mp3_original_reference_container(
    tmp_path: Path,
) -> None:
    names = (
        "video.mp4",
        "karaoke.m4a",
        "original.mp3",
        "timing.json",
        "plan.json",
        "release.json",
        "template.json",
    )
    paths = [tmp_path / name for name in names]
    for path in paths:
        path.write_bytes(b"x")
    request = build_package_request(
        {"producer": {"name": "LyricRail Studio"}},
        playback_video=paths[0],
        karaoke_audio=paths[1],
        original_audio=paths[2],
        lyrics_timing=paths[3],
        render_plan=paths[4],
        release_metadata_file=paths[5],
        presentation_template=paths[6],
        minimum_player_version="0.8.0",
    )
    original = request["assets"][2]
    assert original["logicalName"] == "audio/original-reference.mp3"
    assert original["mediaType"] == "audio/mpeg"
