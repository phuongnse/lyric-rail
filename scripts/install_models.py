#!/usr/bin/env python3
"""Download the exact model revisions declared by LyricRail and verify them."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen


PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from lyricrail.config import load_project_config  # noqa: E402
from lyricrail.model_provenance import (  # noqa: E402
    assert_model_provenance,
    load_model_manifest,
    valid_pinned_audio_filename,
    valid_pinned_audio_url,
    verify_model_provenance,
)


DOWNLOAD_CHUNK_BYTES = 1024 * 1024
DOWNLOAD_TIMEOUT_SECONDS = 300


def _emit(json_lines: bool, message: str, progress_percent: float) -> None:
    if json_lines:
        print(
            json.dumps(
                {
                    "kind": "lyricrail.model-install.progress",
                    "message": message,
                    "progressPercent": max(0.0, min(100.0, progress_percent)),
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            flush=True,
        )
    else:
        print(message, flush=True)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(8 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _audio_files(model: dict[str, Any]) -> list[tuple[str, str, int, str]]:
    filename = str(model["filename"])
    hashes = {filename: str(model["sha256"]).lower()}
    hashes.update(
        (str(name), str(value).lower())
        for name, value in dict(model.get("associatedFileSha256", {})).items()
    )
    urls = dict(model["downloadUrls"])
    sizes = dict(model["fileSizeBytes"])
    if set(hashes) != set(map(str, urls)) or set(hashes) != set(map(str, sizes)):
        raise ValueError("Pinned audio download metadata is incomplete")
    return [
        (name, str(urls[name]), int(sizes[name]), expected)
        for name, expected in hashes.items()
    ]


def _cached_file_matches(path: Path, expected_size: int, expected_sha256: str) -> bool:
    if path.is_symlink():
        return False
    if not path.exists():
        return False
    if not path.is_file():
        raise ValueError(f"Model cache entry is not a regular file: {path.name}")
    return path.stat().st_size == expected_size and _sha256(path) == expected_sha256


def _format_bytes(value: int) -> str:
    for suffix, scale in (("G", 1024**3), ("M", 1024**2), ("k", 1024)):
        if value >= scale:
            rendered = value / scale
            return f"{rendered:.2f}".rstrip("0").rstrip(".") + suffix
    return str(value)


def _open_download(request: Request) -> Any:
    return urlopen(request, timeout=DOWNLOAD_TIMEOUT_SECONDS)  # noqa: S310


def _download_verified_file(
    *,
    key: str,
    filename: str,
    url: str,
    expected_size: int,
    expected_sha256: str,
    destination: Path,
    json_lines: bool,
    progress_percent: float,
) -> None:
    if (
        not valid_pinned_audio_filename(filename)
        or not valid_pinned_audio_url(url, filename)
        or not (0 < expected_size <= 8 * 1024 * 1024 * 1024)
        or len(expected_sha256) != 64
        or any(character not in "0123456789abcdef" for character in expected_sha256)
    ):
        raise ValueError(f"Pinned download metadata is invalid for {key}/{filename}")
    repairing = destination.exists() or destination.is_symlink()
    _emit(
        json_lines,
        f"{'Repairing incomplete cached file' if repairing else 'Downloading verified file'} for {key}: {filename}",
        progress_percent,
    )
    request = Request(url, headers={"User-Agent": "LyricRail/0.8 model-installer"})
    temporary: Path | None = None
    try:
        with _open_download(request) as response:
            declared = response.headers.get("Content-Length")
            if declared is None or not declared.isdigit() or int(declared) != expected_size:
                raise ValueError(
                    f"Pinned download length mismatch for {key}/{filename}: "
                    f"expected {expected_size}, server declared {declared or 'none'}"
                )
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=f".{filename}.", suffix=".part", dir=destination.parent
            )
            temporary = Path(temporary_name)
            digest = hashlib.sha256()
            downloaded = 0
            last_percent = -1
            with os.fdopen(descriptor, "wb") as output:
                while chunk := response.read(DOWNLOAD_CHUNK_BYTES):
                    downloaded += len(chunk)
                    if downloaded > expected_size:
                        raise ValueError(
                            f"Pinned download exceeded its byte limit for {key}/{filename}"
                        )
                    output.write(chunk)
                    digest.update(chunk)
                    percent = int(downloaded * 100 / expected_size)
                    if percent > last_percent:
                        print(
                            f"{percent}%| | {_format_bytes(downloaded)}/{_format_bytes(expected_size)} [verified download]",
                            file=sys.stderr,
                            flush=True,
                        )
                        last_percent = percent
                output.flush()
                os.fsync(output.fileno())
            actual_sha256 = digest.hexdigest()
            if downloaded != expected_size:
                raise ValueError(
                    f"Pinned download is incomplete for {key}/{filename}: "
                    f"expected {expected_size} bytes, received {downloaded}"
                )
            if actual_sha256 != expected_sha256:
                raise ValueError(
                    f"Pinned download hash mismatch for {key}/{filename}: "
                    f"expected {expected_sha256}, got {actual_sha256}"
                )
        if temporary is None:
            raise RuntimeError(f"Pinned download staging failed for {key}/{filename}")
        os.replace(temporary, destination)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json-lines", action="store_true")
    args = parser.parse_args(argv)
    try:
        from huggingface_hub import snapshot_download
    except ImportError as exc:
        print(
            "Install the media, separation, and alignment extras before models: "
            f"{exc}",
            file=sys.stderr,
        )
        return 2

    try:
        manifest = load_model_manifest(PROJECT_ROOT)
        pipeline = load_project_config(PROJECT_ROOT)["pipeline"]
        preflight = verify_model_provenance(
            PROJECT_ROOT,
            pipeline,
            require_files=False,
            verify_hashes=False,
        )
        if not preflight["valid"]:
            raise ValueError(
                "Model manifest validation failed: " + "; ".join(preflight["errors"])
            )
        audio_directory = PROJECT_ROOT / "models" / "audio-separator"
        audio_directory.mkdir(parents=True, exist_ok=True)
        huggingface_cache = PROJECT_ROOT / "models" / "huggingface"
        huggingface_cache.mkdir(parents=True, exist_ok=True)
        models = list(manifest["models"].items())
        total = len(models)

        for index, (key, model) in enumerate(models):
            kind = str(model["type"])
            model_progress = index / max(1, total) * 90.0
            _emit(
                args.json_lines,
                f"Checking pinned model {index + 1} of {total}: {key}",
                model_progress,
            )
            if kind == "audio-separator-checkpoint":
                for filename, url, expected_size, expected_sha256 in _audio_files(model):
                    destination = audio_directory / filename
                    if _cached_file_matches(
                        destination, expected_size, expected_sha256
                    ):
                        continue
                    _download_verified_file(
                        key=key,
                        filename=filename,
                        url=url,
                        expected_size=expected_size,
                        expected_sha256=expected_sha256,
                        destination=destination,
                        json_lines=args.json_lines,
                        progress_percent=model_progress,
                    )
            elif kind == "huggingface-snapshot":
                repository = str(model["repository"])
                revision = str(model["revision"])
                snapshot_download(
                    repo_id=repository,
                    revision=revision,
                    cache_dir=huggingface_cache,
                )
            else:
                raise ValueError(f"Unsupported model type in manifest: {kind}")

        _emit(args.json_lines, "Verifying every pinned model hash", 95.0)
        report = assert_model_provenance(PROJECT_ROOT, pipeline, verify_hashes=True)
        _emit(
            args.json_lines,
            f"Verified {len(report['checks'])} pinned models",
            100.0,
        )
        return 0
    except URLError:
        print(
            "Model installation failed: a pinned HTTPS download did not complete. Retry when the network is available.",
            file=sys.stderr,
            flush=True,
        )
        return 1
    except OSError:
        print(
            "Model installation failed: the verified model cache could not be updated.",
            file=sys.stderr,
            flush=True,
        )
        return 1
    except (RuntimeError, ValueError) as exc:
        print(f"Model installation failed: {exc}", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
