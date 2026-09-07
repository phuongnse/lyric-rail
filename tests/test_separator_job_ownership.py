import os
import shutil
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

from lyricrail import local_pipeline as pipeline


def test_reused_separator_writes_only_into_current_job_including_retry(tmp_path: Path) -> None:
    writes: list[Path] = []
    loads: list[str] = []

    class Separator:
        def __init__(self, *, output_dir, **_):
            self.output_dir = output_dir
            self.model_instance = None

        def load_model(self, **_):
            loads.append(self.output_dir)
            self.model_instance = SimpleNamespace(output_dir=self.output_dir)

        def separate(self, *_):
            # The pinned dependency delegates writes to this independently stored directory.
            directory = Path(self.model_instance.output_dir)
            directory.mkdir(parents=True, exist_ok=True)
            for name in ("vocals.flac", "instrumental.flac"):
                path = directory / name
                path.write_bytes(b"synthetic stem")
                writes.append(path)
            return ["vocals.flac", "instrumental.flac"]

    package = ModuleType("audio_separator")
    module = ModuleType("audio_separator.separator")
    module.Separator = Separator
    fake_torch = SimpleNamespace(load=lambda *_: None)
    contexts = [SimpleNamespace(job_directory=tmp_path / name, checkpoint=lambda: None,
                               progress=lambda *_: None, log=lambda *_: None) for name in ("first", "second")]
    with (
        patch.dict(sys.modules, {"audio_separator": package, "audio_separator.separator": module, "torch": fake_torch}),
        patch.dict(os.environ, {"LYRICRAIL_PERSISTENT_WORKER": "1"}),
        patch.object(pipeline, "_SEPARATION_MODEL_CACHE", {}),
        patch.object(pipeline, "_project_root", return_value=tmp_path),
        patch.object(pipeline, "load_project_config", return_value={"pipeline": {"audioSeparation": {}}}),
        patch.object(pipeline, "_ffmpeg", return_value="ffmpeg"),
        patch.object(pipeline, "_prepend_process_path"),
        patch.object(pipeline, "_prepare_cuda_runtime", return_value=False),
        patch.object(pipeline, "_source_audio", return_value=tmp_path / "source.wav"),
        patch.object(pipeline, "stem_separation_qc", return_value={"errors": []}),
    ):
        pipeline._separate_stems(contexts[0])
        assert contexts[0].job_directory.resolve().is_relative_to(tmp_path.resolve())
        shutil.rmtree(contexts[0].job_directory)  # This test owns only its synthetic temp tree.
        pipeline._separate_stems(contexts[1])
        pipeline._separate_stems(contexts[1])
    assert len(loads) == 1
    assert not contexts[0].job_directory.exists()
    assert all(path.is_relative_to(contexts[1].job_directory) for path in writes[2:])
