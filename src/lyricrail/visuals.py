from __future__ import annotations

import hashlib
import json
import math
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import quote_plus, urljoin
from urllib.request import Request, urlopen


MIXKIT_BASE = "https://mixkit.co"
MIXKIT_LICENSE = "https://mixkit.co/license/#videoFree"
USER_AGENT = "Mozilla/5.0 (compatible; LyricRail/0.5)"
LANDSCAPE_TERMS = {
    "aerial", "beach", "cloud", "coast", "country", "dawn", "desert",
    "field", "forest", "hill", "horizon", "island",
    "lake", "landscape", "meadow", "mist", "moon", "mountain", "nature",
    "ocean", "rain", "river", "road", "sea", "sky", "snow", "sunrise",
    "sunset", "valley", "waterfall", "waves", "woods",
}
NON_LANDSCAPE_TERMS = {
    "boy", "car", "couple", "face", "girl", "hand", "ink", "man", "mirror",
    "people", "person", "portrait", "woman",
}


@dataclass(frozen=True)
class StockVideo:
    asset_id: str
    title: str
    page_url: str
    download_url: str
    query: str


def _normalized(text: str) -> str:
    return re.sub(r"\s+", " ", text.casefold()).strip()


def infer_landscape_queries(title: str, lyrics: str) -> list[str]:
    """Map the song's actual language and emotional vocabulary to visual searches."""
    text = _normalized(f"{title} {lyrics}")
    themed: list[tuple[tuple[str, ...], str]] = [
        (("xa", "chia", "quên", "biệt", "lạ", "goodbye"), "lonely road distant horizon"),
        (("buồn", "sầu", "đau", "khóc", "xót", "sorrow"), "rainy window moody landscape"),
        (("đêm", "trăng", "sao", "night", "moon"), "moonlit mountains night sky"),
        (("biển", "sóng", "bờ", "sea", "ocean"), "cinematic ocean waves dusk"),
        (("mưa", "rain"), "rain clouds over green landscape"),
        (("nhớ", "kỷ niệm", "memory"), "misty forest nostalgic sunrise"),
        (("yêu", "tình", "love"), "warm sunset meadow cinematic"),
        (("quê", "đường", "về", "road", "home"), "country road through scenic fields"),
        (("hy vọng", "bình minh", "mai", "hope", "dawn"), "mountain sunrise morning mist"),
    ]
    queries = [query for words, query in themed if any(word in text for word in words)]
    # These remain content-neutral categories, but the asset selection is salted
    # by the song identity so different songs do not receive one fixed clip pack.
    queries.extend(
        [
            "cinematic natural landscape",
            "slow aerial mountains clouds",
            "peaceful forest light",
            "dramatic coast sunset",
        ]
    )
    return list(dict.fromkeys(queries))[:8]


def _fetch_text(url: str, timeout: float = 30.0) -> str:
    request = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed HTTPS provider
        return response.read().decode("utf-8", errors="replace")


def mixkit_search(query: str) -> list[tuple[str, str]]:
    html = _fetch_text(f"{MIXKIT_BASE}/free-stock-video/?q={quote_plus(query)}")
    matches = re.findall(
        r'href=["\'](?P<href>/free-stock-video/(?P<slug>[^"\'?#]+)-(?P<id>\d+)/)["\']',
        html,
        flags=re.IGNORECASE,
    )
    results: list[tuple[str, str]] = []
    for href, slug, _asset_id in matches:
        page = urljoin(MIXKIT_BASE, href)
        title = slug.replace("-", " ").strip()
        words = set(re.findall(r"[a-z]+", title.casefold()))
        if not words.intersection(LANDSCAPE_TERMS) or words.intersection(NON_LANDSCAPE_TERMS):
            continue
        if (page, title) not in results:
            results.append((page, title))
    return results


def stock_video_from_page(page_url: str, title: str, query: str) -> StockVideo:
    match = re.search(r"-(\d+)/?$", page_url)
    if not match:
        raise ValueError(f"Cannot infer Mixkit asset ID from: {page_url}")
    asset_id = match.group(1)
    html = _fetch_text(page_url)
    direct_urls = re.findall(
        rf"https://assets\.mixkit\.co/videos/{asset_id}/{asset_id}-(?:1080|720)\.mp4",
        html,
    )
    preferred = f"https://assets.mixkit.co/videos/{asset_id}/{asset_id}-1080.mp4"
    download_url = next((url for url in direct_urls if "-1080.mp4" in url), preferred)
    return StockVideo(asset_id, title, page_url, download_url, query)


def select_stock_videos(title: str, lyrics: str, count: int) -> list[StockVideo]:
    queries = infer_landscape_queries(title, lyrics)
    salt = int(hashlib.sha256(f"{title}\n{lyrics}".encode("utf-8")).hexdigest()[:8], 16)
    candidates: list[tuple[str, str, str]] = []
    seen_pages: set[str] = set()
    for query_index, query in enumerate(queries):
        results = mixkit_search(query)
        if results:
            rotation = (salt + query_index * 7) % len(results)
            results = results[rotation:] + results[:rotation]
        for page, asset_title in results[: max(5, math.ceil(count / len(queries)) + 3)]:
            if page in seen_pages:
                continue
            seen_pages.add(page)
            candidates.append((page, asset_title, query))
    target_count = min(count, len(candidates))
    if target_count < 2:
        raise RuntimeError("Mixkit returned fewer than two valid landscape assets.")
    selected: list[StockVideo] = []
    for page, asset_title, query in candidates:
        try:
            selected.append(stock_video_from_page(page, asset_title, query))
        except (OSError, ValueError):
            continue
        if len(selected) >= target_count:
            break
    if len(selected) < 2:
        raise RuntimeError("Fewer than two downloadable Mixkit landscapes were resolved.")
    return selected


def download_stock_video(asset: StockVideo, directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    output = directory / f"mixkit-{asset.asset_id}.mp4"
    if output.is_file() and output.stat().st_size > 100_000:
        return output
    request = Request(asset.download_url, headers={"User-Agent": USER_AGENT})
    temporary = output.with_suffix(".partial")
    temporary.unlink(missing_ok=True)
    try:
        with urlopen(request, timeout=90) as response, temporary.open("wb") as handle:  # noqa: S310
            while chunk := response.read(1024 * 1024):
                handle.write(chunk)
        if temporary.stat().st_size < 100_000:
            raise RuntimeError(f"Downloaded stock video is unexpectedly small: {asset.download_url}")
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)
    return output


def _probe_duration(ffprobe: str, path: Path) -> float:
    completed = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=nw=1:nk=1",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return float(completed.stdout.strip())


def render_landscape_montage(
    *,
    ffmpeg: str,
    ffprobe: str,
    clips: list[Path],
    assets: list[StockVideo],
    output: Path,
    duration_seconds: float,
    work_directory: Path,
    fps: int = 30,
    crossfade_seconds: float = 0.6,
    run: Callable[[list[str]], None] | None = None,
) -> dict:
    if len(clips) < 2 or len(clips) != len(assets):
        raise ValueError("Landscape montage needs at least two matched clips/assets.")
    source_durations = [_probe_duration(ffprobe, path) for path in clips]
    scene_duration = (
        duration_seconds + crossfade_seconds * (len(clips) - 1)
    ) / len(clips)
    if scene_duration <= crossfade_seconds:
        raise ValueError("Invalid landscape scene duration.")

    graph: list[str] = []
    for index, source_duration in enumerate(source_durations):
        available_offset = max(0.0, source_duration - scene_duration)
        offset = available_offset * ((int(assets[index].asset_id) * 37) % 101) / 100
        graph.append(
            f"[{index}:v]trim=start={offset:.6f}:duration={scene_duration:.6f},"
            "setpts=PTS-STARTPTS,"
            "scale=1920:1080:force_original_aspect_ratio=increase,"
            "crop=1920:1080,setsar=1,"
            f"fps={fps},eq=contrast=1.025:saturation=0.94:gamma=0.99,"
            f"trim=duration={scene_duration:.6f},setpts=PTS-STARTPTS[v{index}]"
        )
    running = scene_duration
    current = "v0"
    scene_starts = [0.0]
    for index in range(1, len(clips)):
        offset = running - crossfade_seconds
        target = f"x{index}"
        graph.append(
            f"[{current}][v{index}]xfade=transition=fade:"
            f"duration={crossfade_seconds:.3f}:offset={offset:.6f}[{target}]"
        )
        scene_starts.append(offset)
        running = offset + scene_duration
        current = target
    graph.append(
        f"[{current}]trim=duration={duration_seconds:.6f},setpts=PTS-STARTPTS,"
        f"fps={fps},fade=t=in:st=0:d=1.0,"
        f"fade=t=out:st={max(0.0, duration_seconds - 1.2):.3f}:d=1.2[video]"
    )
    filter_graph = ";".join(graph)
    script = work_directory / "landscape.ffscript"
    script.write_text(filter_graph + "\n", encoding="utf-8")
    command = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y"]
    for clip in clips:
        # Mixkit clips vary in length. Looping short sources keeps all scenes at
        # natural speed instead of producing cheap-looking slow motion.
        command.extend(["-stream_loop", "-1", "-i", str(clip)])
    command.extend(
        [
            "-filter_complex",
            filter_graph,
            "-map",
            "[video]",
            "-an",
            "-c:v",
            "libx264",
            "-profile:v",
            "high",
            "-preset",
            "medium",
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
            "-color_primaries",
            "bt709",
            "-color_trc",
            "bt709",
            "-colorspace",
            "bt709",
            "-movflags",
            "+faststart",
            str(output),
        ]
    )
    if run:
        run(command)
    else:
        subprocess.run(command, check=True)
    if not output.is_file() or output.stat().st_size < 1_000_000:
        raise RuntimeError("Landscape montage render did not produce a valid video.")

    return {
        "schemaVersion": 1,
        "strategy": "content-derived-semantic-landscape",
        "provider": "Mixkit",
        "license": {"name": "Mixkit Stock Video Free License", "url": MIXKIT_LICENSE},
        "outputDurationSeconds": duration_seconds,
        "playbackSpeed": 1.0,
        "sceneDurationSeconds": round(scene_duration, 6),
        "crossfadeSeconds": crossfade_seconds,
        "scenes": [
            {
                "scene": index + 1,
                "startSeconds": round(scene_starts[index], 3),
                "query": assets[index].query,
                "assetId": assets[index].asset_id,
                "title": assets[index].title,
                "page": assets[index].page_url,
                "download": assets[index].download_url,
            }
            for index in range(len(assets))
        ],
    }


def prepare_content_landscape(
    *,
    ffmpeg: str,
    ffprobe: str,
    title: str,
    lyrics: str,
    duration_seconds: float,
    output: Path,
    work_directory: Path,
    target_scene_seconds: float = 22.0,
    minimum_scenes: int = 6,
    maximum_scenes: int = 16,
    fps: int = 30,
    crossfade_seconds: float = 0.6,
    progress: Callable[[int, int, str], None] | None = None,
    run: Callable[[list[str]], None] | None = None,
) -> dict:
    scene_count = max(
        minimum_scenes,
        min(maximum_scenes, math.ceil(duration_seconds / target_scene_seconds)),
    )
    assets = select_stock_videos(title, lyrics, scene_count)
    if len(assets) < min(minimum_scenes, scene_count):
        raise RuntimeError(
            f"Only {len(assets)} valid landscapes were found; "
            f"at least {minimum_scenes} are required."
        )
    clip_directory = work_directory / "clips"
    clips: list[Path] = []
    for index, asset in enumerate(assets, start=1):
        if progress:
            progress(index, len(assets), asset.title)
        clips.append(download_stock_video(asset, clip_directory))
    plan = render_landscape_montage(
        ffmpeg=ffmpeg,
        ffprobe=ffprobe,
        clips=clips,
        assets=assets,
        output=output,
        duration_seconds=duration_seconds,
        work_directory=work_directory,
        fps=fps,
        crossfade_seconds=crossfade_seconds,
        run=run,
    )
    plan["requestedSceneCount"] = scene_count
    plan["selectedSceneCount"] = len(assets)
    manifest = work_directory / "landscape-license-manifest.json"
    manifest.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return plan
