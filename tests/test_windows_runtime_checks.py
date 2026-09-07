from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]


def load_script(name: str):
    path = ROOT / "scripts" / name
    spec = importlib.util.spec_from_file_location(path.stem, path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_python_identity_rejects_wrong_version_architecture_and_layout(tmp_path) -> None:
    checker = load_script("check_windows_python.py")
    expected = tmp_path / ".venv"
    identity = {
        "version": "3.11",
        "pointerBits": 32,
        "platform": "linux",
        "prefix": "/tmp/.venv",
        "basePrefix": "/usr",
        "executable": "/tmp/.venv/bin/python",
        "hasPyvenvConfig": False,
    }
    errors = checker.validate_identity(identity, expected)
    assert any("Python 3.12" in error for error in errors)
    assert any("64-bit" in error for error in errors)
    assert any("native Windows" in error for error in errors)
    assert any("pyvenv.cfg" in error for error in errors)


def test_hidden_windows_worker_uses_strict_utf8_stdio_for_vietnamese_json() -> None:
    if sys.platform != "win32":
        pytest.skip("CREATE_NO_WINDOW is a Windows process boundary")
    request = {
        "requestId": "local-fixture",
        "mediaPath": r"\\?\C:\Music\Đan Nguyên - Truyện Tình Nghèo.mp4",
        "lyricsPath": r"C:\LyricRail\lyrics.txt",
        "title": "Đan Nguyên – Truyện Tình Nghèo",
    }
    probe = (
        "import json,sys; "
        "request=json.load(sys.stdin); "
        "sys.stderr.write('Đường kiểm tra stderr\\n'); sys.stderr.flush(); "
        "print(json.dumps({'stdinEncoding':sys.stdin.encoding,"
        "'stdinErrors':sys.stdin.errors,'stdoutEncoding':sys.stdout.encoding,"
        "'stdoutErrors':sys.stdout.errors,'stderrEncoding':sys.stderr.encoding,"
        "'stderrErrors':sys.stderr.errors,'request':request},ensure_ascii=True),flush=True)"
    )
    environment = os.environ.copy()
    environment.update({"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8:strict"})
    completed = subprocess.run(
        [sys.executable, "-s", "-X", "utf8", "-c", probe],
        input=json.dumps(request, ensure_ascii=False).encode("utf-8"),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        creationflags=subprocess.CREATE_NO_WINDOW,
        env=environment,
    )
    assert completed.returncode == 0, completed.stderr.decode("utf-8", errors="replace")
    result = json.loads(completed.stdout.decode("utf-8", errors="strict"))
    assert result["stdinEncoding"].lower().replace("_", "-") == "utf-8"
    assert result["stdoutEncoding"].lower().replace("_", "-") == "utf-8"
    assert result["stderrEncoding"].lower().replace("_", "-") == "utf-8"
    assert result["stdinErrors"] == "strict"
    assert result["stdoutErrors"] == "strict"
    assert result["stderrErrors"] == "backslashreplace"
    assert completed.stderr.decode("utf-8", errors="strict").replace("\r\n", "\n") == (
        "Đường kiểm tra stderr\n"
    )
    assert result["request"] == request


def acceleration_state(*, nvidia: bool, conflicting_ort: bool = False) -> dict:
    tag = "cu130" if nvidia else "cpu"
    return {
        "distributions": {
            "torch": f"2.13.0+{tag}",
            "torchaudio": f"2.11.0+{tag}",
            "torchvision": f"0.28.0+{tag}",
            "onnxruntime": "1.28.0" if (not nvidia or conflicting_ort) else None,
            "onnxruntime-gpu": "1.29.0" if nvidia else None,
        },
        "errors": [],
        "torch": {
            "moduleVersion": f"2.13.0+{tag}",
            "cudaVersion": "13.0" if nvidia else None,
            "cudaAvailable": nvidia,
            "deviceCount": 1 if nvidia else 0,
            "deviceName": "NVIDIA test device" if nvidia else None,
        },
        "torchaudio": {"moduleVersion": f"2.11.0+{tag}"},
        "torchvision": {"moduleVersion": f"0.28.0+{tag}"},
        "onnxruntime": {
            "moduleVersion": "1.29.0" if nvidia else "1.28.0",
            "providers": (
                ["CUDAExecutionProvider", "CPUExecutionProvider"]
                if nvidia
                else ["CPUExecutionProvider"]
            ),
        },
    }


def validate(checker, state: dict, expected: str) -> list[str]:
    return checker.validate_state(
        state,
        expected=expected,
        torch_version="2.13.0",
        torchaudio_version="2.11.0",
        torchvision_version="0.28.0",
        onnx_version="1.29.0" if expected == "nvidia" else "1.28.0",
        build_tag="cu130" if expected == "nvidia" else "cpu",
        cuda_major="13" if expected == "nvidia" else None,
    )


def test_acceleration_checker_accepts_mutually_exclusive_cpu_and_nvidia_states() -> None:
    checker = load_script("check_windows_acceleration.py")
    assert validate(checker, acceleration_state(nvidia=False), "cpu") == []
    assert validate(checker, acceleration_state(nvidia=True), "nvidia") == []


def test_acceleration_checker_rejects_overlapping_onnx_distributions() -> None:
    checker = load_script("check_windows_acceleration.py")
    errors = validate(
        checker,
        acceleration_state(nvidia=True, conflicting_ort=True),
        "nvidia",
    )
    assert "CPU onnxruntime must not coexist with onnxruntime-gpu" in errors


def test_acceleration_checker_rejects_false_nvidia_claim() -> None:
    checker = load_script("check_windows_acceleration.py")
    state = acceleration_state(nvidia=True)
    state["torch"]["cudaAvailable"] = False
    state["torch"]["deviceCount"] = 0
    state["onnxruntime"]["providers"] = ["CPUExecutionProvider"]
    errors = validate(checker, state, "nvidia")
    assert "PyTorch CUDA backend is unavailable" in errors
    assert "PyTorch did not expose an NVIDIA device" in errors
    assert "ONNX Runtime CUDAExecutionProvider is unavailable" in errors
