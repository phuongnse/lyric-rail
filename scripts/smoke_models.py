#!/usr/bin/env python3
"""Load every pinned model once without processing media."""

from __future__ import annotations

import gc
import os
import sys
from pathlib import Path


SOURCE_PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIGURED_PROJECT_ROOT = Path(
    os.environ.get("LYRICRAIL_HOME", str(SOURCE_PROJECT_ROOT))
).expanduser().resolve()
if (CONFIGURED_PROJECT_ROOT / "src" / "lyricrail").is_dir():
    sys.path.insert(0, str(CONFIGURED_PROJECT_ROOT / "src"))

from lyricrail.config import (  # noqa: E402
    load_project_config,
    load_project_environment,
    resolve_data_root,
    resolve_environment_path,
)
from lyricrail.model_provenance import (  # noqa: E402
    assert_model_provenance,
    load_model_manifest,
)
from lyricrail.song_alignment import VietnameseSongAligner  # noqa: E402


def main() -> int:
    root = load_project_environment(CONFIGURED_PROJECT_ROOT)
    data_root = resolve_data_root(root)
    pipeline = load_project_config(root)["pipeline"]
    assert_model_provenance(root, pipeline, verify_hashes=True)
    ffmpeg = resolve_environment_path(
        "LYRICRAIL_FFMPEG", root, "tools/ffmpeg/bin/ffmpeg.exe"
    )
    if ffmpeg.is_file():
        os.environ["PATH"] = str(ffmpeg.parent) + os.pathsep + os.environ.get(
            "PATH", ""
        )

    import torch
    from audio_separator.separator import Separator
    from transformers import AutoFeatureExtractor, WavLMForXVector

    aligner = VietnameseSongAligner(root, pipeline["lyrics"])
    print(
        "Loaded forced aligner: "
        f"{aligner.model_id}@{aligner.model_revision} on {aligner.device}"
    )
    del aligner
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()

    roles = pipeline["roles"]
    repository = str(roles["speakerEmbeddingModel"])
    revision = str(roles["speakerEmbeddingModelRevision"])
    snapshot = (
        root
        / "models"
        / "huggingface"
        / ("models--" + repository.replace("/", "--"))
        / "snapshots"
        / revision
    )
    extractor = AutoFeatureExtractor.from_pretrained(snapshot, local_files_only=True)
    device = "cuda" if torch.cuda.is_available() else "cpu"
    speaker_model = (
        WavLMForXVector.from_pretrained(snapshot, local_files_only=True)
        .eval()
        .to(device)
    )
    print(f"Loaded speaker embedder: {repository}@{revision} on {device}")
    del speaker_model, extractor
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()

    original_torch_load = torch.load

    def compatible_torch_load(*args: object, **kwargs: object) -> object:
        kwargs.setdefault("weights_only", False)
        return original_torch_load(*args, **kwargs)

    torch.load = compatible_torch_load
    try:
        manifest = load_model_manifest(root)
        filenames = [
            str(model["filename"])
            for model in manifest["models"].values()
            if model["type"] == "audio-separator-checkpoint"
        ]
        for filename in filenames:
            separator = Separator(
                model_file_dir=str(root / "models" / "audio-separator"),
                output_dir=str(data_root / "cache" / "model-smoke"),
                output_format="FLAC",
                use_autocast=True,
            )
            separator.load_model(model_filename=filename)
            print(f"Loaded separator checkpoint: {filename}")
            del separator
            gc.collect()
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
    finally:
        torch.load = original_torch_load
    print("All pinned models loaded successfully.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
