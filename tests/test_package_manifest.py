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


def test_release_metadata_sanitizes_only_the_non_authoritative_filename_label() -> None:
    source = "/media/song\udc90.mp4"
    job = {
        "jobId": "job-1",
        "runtime": {"lyricRailVersion": "0.8.0"},
        "request": {
            "sourceOrigin": "local",
            "sourceMedia": source,
            "sourceRange": {},
            "lyrics": {"sha256": "abc", "lineCount": 1, "wordCount": 2},
        },
    }
    release = build_release_metadata(
        job,
        {
            "source": {
                "songTitle": "Bài\udc90 hát",
                "referenceArtist": "\ud83d\ude00 Ca sĩ",
                "composer": "Nhạc\ud800 sĩ",
            }
        },
        {},
        {"quality": {}},
    )
    assert release["sources"][0]["fileName"] == "song\ufffd.mp4"
    assert release["title"] == "Bài\ufffd hát"
    assert release["referenceArtist"] == "😀 Ca sĩ"
    assert release["composer"] == "Nhạc\ufffd sĩ"
    assert release["credits"][0]["name"] == "😀 Ca sĩ"
    assert source not in str(release)


def test_package_request_contains_dynamic_lyrics_and_dual_audio_metadata(
    tmp_path: Path,
) -> None:
    paths = []
    for name in (
        "video.mp4",
        "karaoke.m4a",
        "original.m4a",
        "authoritative.txt",
        "timing.json",
        "plan.json",
        "release.json",
        "template.json",
        "thumbnail.webp",
        "thumbnail-base.webp",
    ):
        path = tmp_path / name
        path.write_bytes(b"x")
        paths.append(path)
    release = {"producer": {"name": "LyricRail Core"}}
    request = build_package_request(
        release,
        playback_video=paths[0],
        karaoke_audio=paths[1],
        original_audio=paths[2],
        authoritative_lyrics=paths[3],
        lyrics_timing=paths[4],
        render_plan=paths[5],
        release_metadata_file=paths[6],
        presentation_template=paths[7],
        thumbnail=paths[8],
        thumbnail_base=paths[9],
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
        "authoritative-lyrics",
        "release-metadata",
        "thumbnail-base",
        "thumbnail",
    }


def test_package_request_preserves_mp3_original_reference_container(
    tmp_path: Path,
) -> None:
    names = (
        "video.mp4",
        "karaoke.m4a",
        "original.mp3",
        "authoritative.txt",
        "timing.json",
        "plan.json",
        "release.json",
        "template.json",
        "thumbnail.webp",
        "thumbnail-base.webp",
    )
    paths = [tmp_path / name for name in names]
    for path in paths:
        path.write_bytes(b"x")
    request = build_package_request(
        {"producer": {"name": "LyricRail Core"}},
        playback_video=paths[0],
        karaoke_audio=paths[1],
        original_audio=paths[2],
        authoritative_lyrics=paths[3],
        lyrics_timing=paths[4],
        render_plan=paths[5],
        release_metadata_file=paths[6],
        presentation_template=paths[7],
        thumbnail=paths[8],
        thumbnail_base=paths[9],
        minimum_player_version="0.8.0",
    )
    original = request["assets"][2]
    assert original["logicalName"] == "audio/original-reference.mp3"
    assert original["mediaType"] == "audio/mpeg"
