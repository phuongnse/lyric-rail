from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def load_fuzz_smoke_module():
    path = ROOT / "scripts" / "run_package_fuzz_smoke.py"
    spec = importlib.util.spec_from_file_location("run_package_fuzz_smoke", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_fuzz_smoke_uses_disposable_corpus_and_cleans_it(monkeypatch, tmp_path) -> None:
    module = load_fuzz_smoke_module()
    temporary_root = tmp_path / "fuzz-run"
    observed: dict[str, object] = {}

    def make_temporary_root(*, prefix: str) -> str:
        assert prefix == "lyric-rail-package-fuzz-"
        temporary_root.mkdir()
        return str(temporary_root)

    def run(command, *, cwd, check):
        observed["command"] = command
        observed["cwd"] = cwd
        observed["check"] = check
        corpus = Path(command[5])
        assert corpus != module.SOURCE_CORPUS
        assert corpus.is_dir()
        (corpus / "generated-by-fuzzer").write_bytes(b"new coverage")
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(module.tempfile, "mkdtemp", make_temporary_root)
    monkeypatch.setattr(module.subprocess, "run", run)

    assert module.main() == 0
    assert observed["cwd"] == ROOT
    assert observed["check"] is False
    assert observed["command"][:5] == [
        "cargo",
        "+nightly-2026-07-01",
        "fuzz",
        "run",
        "package_open",
    ]
    assert not temporary_root.exists()
