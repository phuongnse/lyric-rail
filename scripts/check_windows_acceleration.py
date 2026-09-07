#!/usr/bin/env python3
"""Verify that one mutually coherent CPU or NVIDIA Python runtime is active."""

from __future__ import annotations

import argparse
from importlib import metadata
import json
from typing import Any


def _distribution_version(name: str) -> str | None:
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return None


def collect_state() -> dict[str, Any]:
    state: dict[str, Any] = {
        "distributions": {
            name: _distribution_version(name)
            for name in (
                "torch",
                "torchaudio",
                "torchvision",
                "onnxruntime",
                "onnxruntime-gpu",
            )
        },
        "errors": [],
    }
    try:
        import torch

        state["torch"] = {
            "moduleVersion": str(torch.__version__),
            "cudaVersion": torch.version.cuda,
            "cudaAvailable": bool(torch.cuda.is_available()),
            "deviceCount": int(torch.cuda.device_count()),
            "deviceName": torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,
        }
    except Exception as error:  # pragma: no cover - native import diagnostics
        state["errors"].append(f"torch import failed: {error}")
        state["torch"] = {}
    for module_name in ("torchaudio", "torchvision"):
        try:
            module = __import__(module_name)
            state[module_name] = {"moduleVersion": str(module.__version__)}
        except Exception as error:  # pragma: no cover - native import diagnostics
            state["errors"].append(f"{module_name} import failed: {error}")
            state[module_name] = {}
    try:
        import onnxruntime

        state["onnxruntime"] = {
            "moduleVersion": str(onnxruntime.__version__),
            "providers": list(onnxruntime.get_available_providers()),
        }
    except Exception as error:  # pragma: no cover - native import diagnostics
        state["errors"].append(f"onnxruntime import failed: {error}")
        state["onnxruntime"] = {"providers": []}
    return state


def validate_state(
    state: dict[str, Any],
    *,
    expected: str,
    torch_version: str,
    torchaudio_version: str,
    torchvision_version: str,
    onnx_version: str,
    build_tag: str,
    cuda_major: str | None,
) -> list[str]:
    errors = list(state.get("errors", []))
    distributions = state.get("distributions", {})
    expected_modules = {
        "torch": f"{torch_version}+{build_tag}",
        "torchaudio": f"{torchaudio_version}+{build_tag}",
        "torchvision": f"{torchvision_version}+{build_tag}",
    }
    for name in ("torch", "torchaudio", "torchvision"):
        expected_distribution = expected_modules[name]
        if distributions.get(name) != expected_distribution:
            errors.append(
                f"expected {name} distribution {expected_distribution}, "
                f"found {distributions.get(name)}"
            )
        if state.get(name, {}).get("moduleVersion") != expected_modules[name]:
            errors.append(
                f"expected {name} build {expected_modules[name]}, "
                f"found {state.get(name, {}).get('moduleVersion')}"
            )
    cpu_ort = distributions.get("onnxruntime")
    gpu_ort = distributions.get("onnxruntime-gpu")
    providers = state.get("onnxruntime", {}).get("providers", [])
    torch_state = state.get("torch", {})
    if expected == "nvidia":
        if cpu_ort is not None:
            errors.append("CPU onnxruntime must not coexist with onnxruntime-gpu")
        if gpu_ort != onnx_version:
            errors.append(f"expected onnxruntime-gpu {onnx_version}, found {gpu_ort}")
        if torch_state.get("cudaAvailable") is not True:
            errors.append("PyTorch CUDA backend is unavailable")
        cuda_version = str(torch_state.get("cudaVersion") or "")
        if not cuda_major or not cuda_version.startswith(f"{cuda_major}."):
            errors.append(f"expected CUDA {cuda_major}.x, found {cuda_version or None}")
        if int(torch_state.get("deviceCount") or 0) < 1:
            errors.append("PyTorch did not expose an NVIDIA device")
        if "CUDAExecutionProvider" not in providers:
            errors.append("ONNX Runtime CUDAExecutionProvider is unavailable")
    else:
        if gpu_ort is not None:
            errors.append("onnxruntime-gpu must not coexist with the CPU runtime")
        if cpu_ort != onnx_version:
            errors.append(f"expected onnxruntime {onnx_version}, found {cpu_ort}")
        if torch_state.get("cudaVersion") is not None:
            errors.append("CPU mode must use a CPU-only PyTorch build")
        if torch_state.get("cudaAvailable") is not False:
            errors.append("CPU mode unexpectedly exposed CUDA")
        if "CUDAExecutionProvider" in providers:
            errors.append("CPU mode unexpectedly exposed ONNX CUDAExecutionProvider")
        if "CPUExecutionProvider" not in providers:
            errors.append("ONNX Runtime CPUExecutionProvider is unavailable")
    return errors


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--expected", choices=("cpu", "nvidia"), required=True)
    parser.add_argument("--torch-version", required=True)
    parser.add_argument("--torchaudio-version", required=True)
    parser.add_argument("--torchvision-version", required=True)
    parser.add_argument("--onnx-version", required=True)
    parser.add_argument("--build-tag", required=True)
    parser.add_argument("--cuda-major")
    options = parser.parse_args(arguments)
    state = collect_state()
    errors = validate_state(
        state,
        expected=options.expected,
        torch_version=options.torch_version,
        torchaudio_version=options.torchaudio_version,
        torchvision_version=options.torchvision_version,
        onnx_version=options.onnx_version,
        build_tag=options.build_tag,
        cuda_major=options.cuda_major,
    )
    print(
        json.dumps(
            {"schemaVersion": 1, "state": state, "errors": errors},
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
