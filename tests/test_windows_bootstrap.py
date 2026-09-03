from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "config" / "windows-dev-tools.json"
BOOTSTRAP = ROOT / "scripts" / "bootstrap_windows.ps1"
ENVIRONMENT = ROOT / "scripts" / "windows_dev_environment.ps1"
LAUNCHER = ROOT / "scripts" / "dev_player_windows.ps1"
WINDOWS_BOOTSTRAP_REQUIREMENTS = ROOT / "requirements" / "windows-bootstrap.txt"
RUST_TOOLCHAIN = ROOT / "rust-toolchain.toml"
PYTHON_CHECK = ROOT / "scripts" / "check_windows_python.py"
ACCELERATION_CHECK = ROOT / "scripts" / "check_windows_acceleration.py"


def test_windows_tool_declaration_covers_the_complete_native_floor() -> None:
    config = json.loads(TOOLS.read_text(encoding="utf-8"))
    assert config["schemaVersion"] == 1
    assert config["architecture"] == "x64"
    assert {package["id"] for package in config["packages"].values()} == {
        "Python.Python.3.12",
        "OpenJS.NodeJS.LTS",
        "Rustlang.Rustup",
        "Microsoft.VisualStudio.2022.BuildTools",
        "Gyan.FFmpeg",
    }
    assert config["rust"] == {
        "stableToolchain": "1.98.0",
        "nightlyToolchain": "nightly-2026-07-01",
        "target": "x86_64-pc-windows-msvc",
        "cargoAuditVersion": "0.22.2",
        "cargoFuzzVersion": "0.13.2",
    }
    assert config["python"]["baseExtras"] == ["bootstrap-common"]
    assert config["python"]["runtime"] == {
        "torchVersion": "2.13.0",
        "torchaudioVersion": "2.11.0",
        "torchvisionVersion": "0.28.0",
        "onnxCpuVersion": "1.28.0",
        "onnxGpuVersion": "1.29.0",
        "cpuBuildTag": "cpu",
        "cpuIndexUrl": "https://download.pytorch.org/whl/cpu",
        "nvidiaBuildTag": "cu130",
        "nvidiaCudaMajor": "13",
        "nvidiaIndexUrl": "https://download.pytorch.org/whl/cu130",
    }


def test_windows_scripts_reject_workaround_toolchains_and_install_all_dependencies() -> None:
    combined = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (BOOTSTRAP, ENVIRONMENT, LAUNCHER, PYTHON_CHECK, ACCELERATION_CHECK)
    ).lower()
    for forbidden in ("wsl.exe", "bash -lc", "/mnt/", "c:\\lrpy"):
        assert forbidden not in combined
    for required in (
        "winget",
        "test-executableversion",
        "requiredversionprefix",
        "verifiedandreputablepolicystate",
        "smart app control is enforcing",
        "visualstudio.component.vc.tools.x86.x64",
        '"-m", "venv"',
        "check_windows_python.py",
        "check_windows_acceleration.py",
        "cudaexecutionprovider",
        "remove mutually exclusive onnx runtime distributions",
        '"npm.cmd"',
        '"ci"',
        '"cargo-audit"',
        '"cargo-fuzz"',
        '"frontend", "python", "rust", "security"',
        ".dev\\target-windows",
    ):
        assert required in combined


def test_windows_bootstrap_pins_and_hashes_its_packaging_tool() -> None:
    requirements = WINDOWS_BOOTSTRAP_REQUIREMENTS.read_text(encoding="utf-8")
    assert "pip==26.2.1" in requirements
    assert "--hash=sha256:71138adf1f4ca900cdb7d289c21b7494329f2332b6d85f0e1c42108c0384ed3e" in requirements
    bootstrap = BOOTSTRAP.read_text(encoding="utf-8").lower()
    assert "requirements\\windows-bootstrap.txt" in bootstrap
    assert '"--upgrade", "--require-hashes", "--only-binary", ":all:"' in bootstrap


def test_shared_rust_pin_does_not_force_a_windows_target_on_other_hosts() -> None:
    toolchain = RUST_TOOLCHAIN.read_text(encoding="utf-8")
    assert 'channel = "1.98.0"' in toolchain
    assert "targets" not in toolchain


def test_windows_environment_exports_resolved_node_for_npm_child_processes() -> None:
    environment = ENVIRONMENT.read_text(encoding="utf-8").lower()
    assert "set-deduplicatedprocesspath" in environment
    assert "if ($node) { add-processpath (split-path $node) }" in environment
    assert "if ($npm) { add-processpath (split-path $npm) }" in environment


@pytest.mark.skipif(os.name != "nt", reason="native PowerShell environment runs on Windows")
def test_windows_environment_path_is_idempotent() -> None:
    command = (
        f"$first = . '{ENVIRONMENT}' -PassThru -AllowMissing; "
        "$firstPath = $env:Path; "
        f"$second = . '{ENVIRONMENT}' -PassThru -AllowMissing; "
        "$secondPath = $env:Path; "
        f"$third = . '{ENVIRONMENT}' -PassThru -AllowMissing; "
        "$entries = @($env:Path -split ';' | Where-Object { $_ }); "
        "$keys = @($entries | ForEach-Object { $_.Trim().TrimEnd('\\').ToLowerInvariant() }); "
        "[pscustomobject]@{ "
        "firstLength = $firstPath.Length; secondPath = $secondPath; thirdPath = $env:Path; "
        "entryCount = $entries.Count; uniqueCount = @($keys | Select-Object -Unique).Count "
        "} | ConvertTo-Json -Compress"
    )
    completed = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            command,
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8-sig",
    )
    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert result["firstLength"] == len(result["secondPath"])
    assert result["secondPath"] == result["thirdPath"]
    assert result["entryCount"] == result["uniqueCount"]


@pytest.mark.skipif(os.name != "nt", reason="native PowerShell bootstrap runs on Windows")
def test_cargo_tool_version_match_is_exact() -> None:
    command = (
        f". '{BOOTSTRAP}' -Plan | Out-Null; "
        "[pscustomobject]@{ "
        "exact = (Test-ExactToolVersionOutput -Name 'cargo-audit' -Version '0.22.2' -Output 'cargo-audit 0.22.2'); "
        "prefixCollision = (Test-ExactToolVersionOutput -Name 'cargo-audit' -Version '0.22.2' -Output 'cargo-audit 0.22.20') "
        "} | ConvertTo-Json -Compress"
    )
    completed = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            command,
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8-sig",
    )
    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert result == {"exact": True, "prefixCollision": False}


@pytest.mark.skipif(
    os.name != "nt" or not (ROOT / ".venv" / "Scripts" / "python.exe").is_file(),
    reason="repository native Windows venv is unavailable",
)
def test_windows_python_checker_accepts_repo_venv_and_rejects_base_python() -> None:
    venv = ROOT / ".venv"
    venv_python = venv / "Scripts" / "python.exe"
    accepted = subprocess.run(
        [str(venv_python), str(PYTHON_CHECK), "--expected-prefix", str(venv)],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert accepted.returncode == 0, accepted.stdout + accepted.stderr
    base_python = (
        Path(os.environ["LOCALAPPDATA"])
        / "Programs"
        / "Python"
        / "Python312"
        / "python.exe"
    )
    rejected = subprocess.run(
        [str(base_python), str(PYTHON_CHECK), "--expected-prefix", str(venv)],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert rejected.returncode == 1
    assert "not running inside a virtual environment" in rejected.stdout


@pytest.mark.skipif(os.name != "nt", reason="native PowerShell plan runs on Windows")
def test_windows_bootstrap_plan_is_read_only_and_machine_parseable() -> None:
    completed = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(BOOTSTRAP),
            "-Plan",
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8-sig",
    )
    assert completed.returncode == 0, completed.stderr
    plan = json.loads(completed.stdout)
    assert plan["platform"] == "windows-x86_64"
    assert plan["mutatesSystem"] is False
    assert plan["profiles"] == ["frontend", "python", "rust", "security"]
    assert plan["pythonRuntime"]["nvidiaIndexUrl"] == "https://download.pytorch.org/whl/cu130"
    assert plan["pythonRuntime"]["onnxGpuVersion"] == "1.29.0"
    assert plan["smartAppControl"]["mode"] in {
        "Off",
        "Enforced",
        "Evaluation",
        "Unknown",
    }
    assert plan["smartAppControl"]["nativeBuildCompatible"] is (
        plan["smartAppControl"]["mode"] != "Enforced"
    )
    assert all(Path(path).is_absolute() for path in plan["generatedRoots"])


def test_player_does_not_present_framework_assets_as_lyricrail_branding() -> None:
    app = (ROOT / "apps" / "player" / "src" / "App.tsx").read_text(encoding="utf-8")
    assert 'className="brand" aria-label="LyricRail"' in app
    assert "assets/brand/lyricrail-mark.svg" in app
    assert "src-tauri/icons/icon.png" not in app
    assert "tauri.svg" not in app


def test_frontend_profile_uses_native_node_wrapper_instead_of_a_windows_batch_file() -> None:
    project = json.loads((ROOT / ".process" / "project.json").read_text(encoding="utf-8"))
    for check in project["profiles"]["frontend"]:
        assert check["run"][:2] == ["python", "scripts/npm_command.py"]
        assert check["run"][2] in {"run", "test", "audit"}
