from __future__ import annotations

import hashlib
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from lyricrail.model_provenance import (
    valid_pinned_audio_filename,
    valid_pinned_audio_url,
    verify_model_provenance,
)


class ModelProvenanceTests(unittest.TestCase):
    def _project(self, directory: str) -> tuple[Path, dict[str, object], Path]:
        root = Path(directory)
        model = root / "models" / "audio-separator" / "model.ckpt"
        model.parent.mkdir(parents=True)
        model.write_bytes(b"immutable checkpoint")
        digest = hashlib.sha256(model.read_bytes()).hexdigest()
        model_config = model.parent / "model.yaml"
        model_config.write_bytes(b"immutable configuration")
        config_digest = hashlib.sha256(model_config.read_bytes()).hexdigest()
        manifest = {
            "schemaVersion": 1,
            "policy": "test",
            "models": {
                "primary": {
                    "type": "audio-separator-checkpoint",
                    "filename": "model.ckpt",
                    "sha256": digest,
                    "associatedFileSha256": {"model.yaml": config_digest},
                    "downloadUrls": {
                        "model.ckpt": "https://github.com/nomadkaraoke/python-audio-separator/releases/download/model-configs/model.ckpt",
                        "model.yaml": "https://github.com/nomadkaraoke/python-audio-separator/releases/download/model-configs/model.yaml",
                    },
                    "fileSizeBytes": {
                        "model.ckpt": model.stat().st_size,
                        "model.yaml": model_config.stat().st_size,
                    },
                    "configPaths": ["audioSeparation.modelFilename"],
                }
            },
        }
        config = root / "config"
        config.mkdir()
        (config / "model-manifest.json").write_text(
            json.dumps(manifest), encoding="utf-8"
        )
        pipeline: dict[str, object] = {
            "audioSeparation": {"modelFilename": "model.ckpt"}
        }
        return root, pipeline, model

    def test_exact_checkpoint_hash_passes(self) -> None:
        with TemporaryDirectory() as directory:
            root, pipeline, _ = self._project(directory)

            report = verify_model_provenance(
                root, pipeline, require_files=True, verify_hashes=True
            )

            self.assertTrue(report["valid"])
            self.assertTrue(report["checks"][0]["verified"])
            self.assertTrue(report["checks"][0]["downloadsPinned"])

    def test_modified_checkpoint_fails(self) -> None:
        with TemporaryDirectory() as directory:
            root, pipeline, model = self._project(directory)
            model.write_bytes(b"modified checkpoint")

            report = verify_model_provenance(
                root, pipeline, require_files=True, verify_hashes=True
            )

            self.assertFalse(report["valid"])
            self.assertIn("hash mismatch", report["errors"][0])

    def test_modified_model_configuration_fails(self) -> None:
        with TemporaryDirectory() as directory:
            root, pipeline, model = self._project(directory)
            (model.parent / "model.yaml").write_bytes(b"modified configuration")

            report = verify_model_provenance(
                root, pipeline, require_files=True, verify_hashes=True
            )

            self.assertFalse(report["valid"])
            self.assertTrue(
                any("configuration hash mismatch" in item for item in report["errors"])
            )

    def test_audio_download_mapping_must_be_complete_safe_and_bounded(self) -> None:
        with TemporaryDirectory() as directory:
            root, pipeline, _ = self._project(directory)
            manifest_path = root / "config" / "model-manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            model = manifest["models"]["primary"]
            model["downloadUrls"]["model.ckpt"] = (
                "https://github.com/evil/repository/releases/download/model-configs/model.ckpt"
            )
            model["fileSizeBytes"]["model.yaml"] = 0
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            report = verify_model_provenance(
                root, pipeline, require_files=False, verify_hashes=False
            )

            self.assertFalse(report["valid"])
            self.assertTrue(
                any("invalid pinned HTTPS URL" in error for error in report["errors"])
            )
            self.assertTrue(any("invalid byte size" in error for error in report["errors"]))

    def test_repository_audio_manifest_pins_every_downloaded_file(self) -> None:
        root = Path(__file__).resolve().parents[1]
        manifest = json.loads(
            (root / "config" / "model-manifest.json").read_text(encoding="utf-8")
        )
        audio_models = [
            model
            for model in manifest["models"].values()
            if model["type"] == "audio-separator-checkpoint"
        ]
        self.assertEqual(len(audio_models), 4)
        for model in audio_models:
            expected_files = {
                model["filename"],
                *model.get("associatedFileSha256", {}).keys(),
            }
            self.assertEqual(set(model["downloadUrls"]), expected_files)
            self.assertEqual(set(model["fileSizeBytes"]), expected_files)
            self.assertTrue(
                all(url.startswith("https://github.com/") for url in model["downloadUrls"].values())
            )
        primary = manifest["models"]["instrumental-primary"]
        self.assertEqual(
            primary["sha256"],
            "16311025a5133ae6411760ccfe9e3e66b31a01d9d8bec0a03fa7ec4bedac7a15",
        )
        self.assertEqual(
            primary["fileSizeBytes"][primary["filename"]], 204_483_033
        )

    def test_windows_unsafe_names_and_non_release_urls_are_rejected(self) -> None:
        for filename in (
            "C:escape.ckpt",
            "model.ckpt:stream",
            "CON",
            "nul.txt",
            "COM1.ckpt",
            "CONOUT$.txt",
            "trailing.",
            "trailing ",
            "control\x1f.ckpt",
            "..",
            "folder/model.ckpt",
            "folder\\model.ckpt",
        ):
            self.assertFalse(valid_pinned_audio_filename(filename), filename)
        filename = "safe_model.ckpt"
        self.assertTrue(valid_pinned_audio_filename(filename))
        valid_url = (
            "https://github.com/nomadkaraoke/python-audio-separator/"
            "releases/download/model-configs/safe_model.ckpt"
        )
        self.assertTrue(valid_pinned_audio_url(valid_url, filename))
        for url in (
            "https://github.com/evil/repository/releases/download/model-configs/safe_model.ckpt",
            "https://github.com:444/nomadkaraoke/python-audio-separator/releases/download/model-configs/safe_model.ckpt",
            valid_url + "?token=secret",
            valid_url.replace("safe_model.ckpt", "other.ckpt"),
        ):
            self.assertFalse(valid_pinned_audio_url(url, filename), url)


if __name__ == "__main__":
    unittest.main()
