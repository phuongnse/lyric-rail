#!/usr/bin/env python3
"""Download the exact model revisions declared by LyricRail and verify them."""

from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from lyricrail.config import load_project_config  # noqa: E402
from lyricrail.model_provenance import (  # noqa: E402
    assert_model_provenance,
    load_model_manifest,
)


def main() -> int:
    try:
        from audio_separator.separator import Separator
        from huggingface_hub import snapshot_download
    except ImportError as exc:
        print(
            "Install the media, separation, and alignment extras before models: "
            f"{exc}",
            file=sys.stderr,
        )
        return 2

    manifest = load_model_manifest(PROJECT_ROOT)
    audio_directory = PROJECT_ROOT / "models" / "audio-separator"
    audio_directory.mkdir(parents=True, exist_ok=True)
    huggingface_cache = PROJECT_ROOT / "models" / "huggingface"
    huggingface_cache.mkdir(parents=True, exist_ok=True)
    separator: Separator | None = None

    for key, model in manifest["models"].items():
        kind = str(model["type"])
        if kind == "audio-separator-checkpoint":
            if separator is None:
                separator = Separator(
                    model_file_dir=str(audio_directory),
                    output_dir=str(PROJECT_ROOT / "cache" / "model-bootstrap"),
                    output_format="FLAC",
                )
            filename = str(model["filename"])
            print(f"Downloading audio checkpoint {key}: {filename}")
            separator.download_model_and_data(filename)
        elif kind == "huggingface-snapshot":
            repository = str(model["repository"])
            revision = str(model["revision"])
            print(f"Downloading Hugging Face snapshot {key}: {repository}@{revision}")
            snapshot_download(
                repo_id=repository,
                revision=revision,
                cache_dir=huggingface_cache,
            )
        else:
            raise ValueError(f"Unsupported model type in manifest: {kind}")

    pipeline = load_project_config(PROJECT_ROOT)["pipeline"]
    report = assert_model_provenance(PROJECT_ROOT, pipeline, verify_hashes=True)
    print(f"Verified {len(report['checks'])} pinned models.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
