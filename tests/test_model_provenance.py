from __future__ import annotations

import hashlib
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from lyricrail.model_provenance import verify_model_provenance


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


if __name__ == "__main__":
    unittest.main()
