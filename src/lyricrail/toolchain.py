from __future__ import annotations

import json
import importlib.util
import os
import platform
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .config import load_project_config
from .model_provenance import verify_model_provenance
from .validation import validate_project


@dataclass(frozen=True)
class Check:
    name: str
    status: str
    required: bool
    detail: str
    path: str = ""


def _resolve_command(command: str, environment_variable: str) -> tuple[str, str]:
    override = os.environ.get(environment_variable, "").strip()
    candidate = override or command
    expanded = str(Path(candidate).expanduser()) if any(
        separator in candidate for separator in ("/", "\\")
    ) else candidate
    resolved = shutil.which(expanded)
    if resolved:
        source = environment_variable if override else "PATH"
        return str(Path(resolved).resolve()), source
    return "", environment_variable if override else "PATH"


def _version_line(executable: str, arguments: list[str]) -> str:
    try:
        completed = subprocess.run(
            [executable, *arguments],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=8,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"unable to read version: {exc}"
    output = completed.stdout.strip().splitlines()
    return output[0].strip() if output else "no version information"


def _command_check(
    name: str,
    command: str,
    environment_variable: str,
    required: bool,
    version_arguments: list[str] | None = None,
) -> Check:
    executable, source = _resolve_command(command, environment_variable)
    if not executable:
        return Check(
            name=name,
            status="missing" if required else "optional",
            required=required,
            detail=f"not found via {source}",
        )
    version = _version_line(executable, version_arguments or ["-version"])
    return Check(
        name=name,
        status="ok",
        required=required,
        detail=f"{version} ({source})",
        path=executable,
    )


def _module_check(name: str, module: str, required: bool) -> Check:
    spec = importlib.util.find_spec(module)
    if spec is None:
        return Check(
            name=name,
            status="missing" if required else "optional",
            required=required,
            detail=f"Python module '{module}' is not installed",
        )
    return Check(
        name=name,
        status="ok",
        required=required,
        detail=f"Python module '{module}' is available",
        path=str(spec.origin or ""),
    )


def detect_torch_backend() -> Check:
    if importlib.util.find_spec("torch") is None:
        return Check("torch_backend", "missing", True, "PyTorch is not installed")
    try:
        import torch

        if torch.cuda.is_available():
            detail = (
                f"PyTorch {torch.__version__}; CUDA {torch.version.cuda}; "
                f"{torch.cuda.get_device_name(0)}"
            )
        else:
            detail = f"PyTorch {torch.__version__}; CPU fallback"
        return Check("torch_backend", "ok", True, detail, str(Path(torch.__file__).resolve()))
    except (ImportError, OSError, RuntimeError) as exc:
        return Check("torch_backend", "missing", True, f"PyTorch import failed: {exc}")


def detect_onnx_backend() -> Check:
    if importlib.util.find_spec("onnxruntime") is None:
        return Check("onnx_backend", "optional", False, "ONNX Runtime is not installed")
    try:
        import onnxruntime

        providers = onnxruntime.get_available_providers()
        backend = "CUDA" if "CUDAExecutionProvider" in providers else "CPU"
        return Check(
            "onnx_backend",
            "ok",
            False,
            f"ONNX Runtime {onnxruntime.__version__}; {backend}; providers={providers}",
            str(Path(onnxruntime.__file__).resolve()),
        )
    except (ImportError, OSError, RuntimeError) as exc:
        return Check("onnx_backend", "optional", False, f"ONNX Runtime import failed: {exc}")


def detect_accelerator() -> Check:
    nvidia_path, _ = _resolve_command("nvidia-smi", "LYRICRAIL_NVIDIA_SMI")
    if nvidia_path:
        detail = _version_line(
            nvidia_path,
            ["--query-gpu=name,memory.total", "--format=csv,noheader"],
        )
        return Check("accelerator", "ok", False, f"CUDA candidate: {detail}", nvidia_path)

    system = platform.system()
    machine = platform.machine().lower()
    if system == "Darwin" and machine in {"arm64", "aarch64"}:
        return Check(
            "accelerator",
            "ok",
            False,
            "Apple Silicon/Metal candidate; backend support will be verified after AI install",
        )
    return Check("accelerator", "optional", False, "CPU mode")


def collect_doctor_report(root: Path, *, production: bool = False) -> dict[str, Any]:
    checks = [
        Check(
            name="python",
            status="ok" if sys.version_info >= (3, 11) else "missing",
            required=True,
            detail=f"Python {platform.python_version()} on {platform.system()} {platform.machine()}",
            path=sys.executable,
        ),
        Check(
            name="python_packaging",
            status=(
                "ok"
                if importlib.util.find_spec("venv") is not None
                and importlib.util.find_spec("pip") is not None
                else "missing"
            ),
            required=True,
            detail=(
                "venv and pip are available"
                if importlib.util.find_spec("venv") is not None
                and importlib.util.find_spec("pip") is not None
                else "Python must include venv and pip"
            ),
            path="",
        ),
        _command_check("ffmpeg", "ffmpeg", "LYRICRAIL_FFMPEG", True),
        _command_check("ffprobe", "ffprobe", "LYRICRAIL_FFPROBE", True),
        detect_accelerator(),
        _module_check("audio_separator", "audio_separator", True),
        _module_check("transformers", "transformers", True),
        _module_check("torchaudio", "torchaudio", True),
        detect_torch_backend(),
        detect_onnx_backend(),
    ]

    project_files = (
        root / "config" / "pipeline.json",
        root / "config" / "model-manifest.json",
        root / "templates" / "karaoke-classic.json",
        root / "vendor" / "lyric-alignment" / "model_handling.py",
        root / "vendor" / "lyric-alignment" / "LICENSE",
    )
    missing_project_files = [str(path) for path in project_files if not path.is_file()]
    checks.append(
        Check(
            name="project",
            status="ok" if not missing_project_files else "missing",
            required=True,
            detail=(
                "project structure is valid"
                if not missing_project_files
                else "missing: " + ", ".join(missing_project_files)
            ),
            path=str(root),
        )
    )

    drive_configured = bool(os.environ.get("LYRICRAIL_GOOGLE_CLIENT_ID", "").strip())
    checks.append(
        Check(
            name="google_drive",
            status="ok" if drive_configured else "optional",
            required=False,
            detail=(
                "desktop Picker client configured"
                if drive_configured
                else "not configured; local library remains available"
            ),
            path="",
        )
    )

    validation = validate_project(root)
    checks.append(
        Check(
            name="configuration",
            status="ok" if validation["valid"] else "missing",
            required=True,
            detail=(
                f"valid ({validation['summary']['warnings']} warnings)"
                if validation["valid"]
                else f"invalid ({validation['summary']['errors']} errors)"
            ),
            path=str(root / "config"),
        )
    )

    if production:
        try:
            pipeline = load_project_config(root)["pipeline"]
            provenance = verify_model_provenance(
                root, pipeline, require_files=True, verify_hashes=True
            )
            detail = (
                f"{len(provenance['checks'])} pinned models verified"
                if provenance["valid"]
                else "; ".join(provenance["errors"])
            )
            checks.append(
                Check(
                    name="model_provenance",
                    status="ok" if provenance["valid"] else "missing",
                    required=True,
                    detail=detail,
                    path=str(root / "config" / "model-manifest.json"),
                )
            )
        except (OSError, ValueError) as exc:
            checks.append(
                Check(
                    name="model_provenance",
                    status="missing",
                    required=True,
                    detail=str(exc),
                    path=str(root / "config" / "model-manifest.json"),
                )
            )

    return {
        "product": "LyricRail",
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "python": platform.python_version(),
        },
        "projectRoot": str(root),
        "productionAudit": production,
        "checks": [asdict(check) for check in checks],
        "ready": all(
            check.status == "ok" for check in checks if check.required
        ),
    }


def print_doctor_report(report: dict[str, Any], as_json: bool = False) -> None:
    if as_json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return

    platform_info = report["platform"]
    print("LyricRail system doctor")
    print(
        f"Platform: {platform_info['system']} {platform_info['release']} "
        f"({platform_info['machine']})"
    )
    print(f"Project:  {report['projectRoot']}")
    for check in report["checks"]:
        marker = {"ok": "OK", "missing": "MISSING", "optional": "OPTIONAL"}[
            check["status"]
        ]
        print(f"[{marker:8}] {check['name']}: {check['detail']}")
        if check["path"]:
            print(f"           {check['path']}")
    print("READY" if report["ready"] else "NOT READY")
