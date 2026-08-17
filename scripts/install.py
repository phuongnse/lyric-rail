#!/usr/bin/env python3
"""Create a local virtual environment and install LyricRail portably."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import venv
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def environment_python(environment: Path) -> Path:
    if os.name == "nt":
        return environment / "Scripts" / "python.exe"
    return environment / "bin" / "python"


def installed_console_script(environment: Path) -> Path:
    if os.name == "nt":
        return environment / "Scripts" / "lyricrail.exe"
    return environment / "bin" / "lyricrail"


def create_stable_launcher(environment: Path) -> Path:
    source = installed_console_script(environment)
    if not source.is_file():
        raise FileNotFoundError(f"Console script was not installed: {source}")
    launcher_directory = PROJECT_ROOT / "bin"
    launcher_directory.mkdir(parents=True, exist_ok=True)
    destination = launcher_directory / source.name
    shutil.copy2(source, destination)
    return destination


def register_windows_environment(launcher_directory: Path) -> None:
    """Register only the app launcher, never the full venv, in User PATH."""
    if os.name != "nt":
        raise OSError("--add-to-path is currently supported on Windows only")

    import ctypes
    import winreg

    with winreg.CreateKeyEx(
        winreg.HKEY_CURRENT_USER,
        "Environment",
        0,
        winreg.KEY_READ | winreg.KEY_SET_VALUE,
    ) as key:
        try:
            current_path, value_type = winreg.QueryValueEx(key, "Path")
        except FileNotFoundError:
            current_path, value_type = "", winreg.REG_EXPAND_SZ
        if value_type not in (winreg.REG_SZ, winreg.REG_EXPAND_SZ):
            value_type = winreg.REG_EXPAND_SZ

        entry = str(launcher_directory.resolve())

        def normalized(value: str) -> str:
            return os.path.normcase(
                os.path.normpath(os.path.expandvars(value.strip().strip('"')))
            )

        entries = [item.strip() for item in current_path.split(os.pathsep) if item.strip()]
        if normalized(entry) not in {normalized(item) for item in entries}:
            entries.append(entry)
            winreg.SetValueEx(key, "Path", 0, value_type, os.pathsep.join(entries))
        winreg.SetValueEx(key, "LYRICRAIL_HOME", 0, winreg.REG_SZ, str(PROJECT_ROOT))

    # Notify Explorer/Terminal so newly opened processes receive the updated values.
    ctypes.windll.user32.SendMessageTimeoutW(
        0xFFFF, 0x001A, 0, "Environment", 0x0002, 5000, None
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Install LyricRail into a local venv")
    parser.add_argument(
        "--venv", type=Path, default=PROJECT_ROOT / ".venv", help="Venv destination"
    )
    parser.add_argument(
        "--extras",
        default="",
        help="Optional dependency group, e.g. youtube or dev",
    )
    parser.add_argument(
        "--add-to-path",
        action="store_true",
        help="Windows: expose the stable bin/ launcher in User PATH",
    )
    args = parser.parse_args()
    destination = args.venv.expanduser().resolve()

    if sys.version_info < (3, 11):
        print("LyricRail requires Python 3.11 or newer.", file=sys.stderr)
        return 2

    if not destination.exists():
        print(f"Creating virtual environment: {destination}")
        try:
            venv.EnvBuilder(with_pip=True).create(destination)
        except (OSError, subprocess.SubprocessError) as exc:
            print(
                "Could not create a venv with pip. Install the complete Python "
                f"distribution (including venv/ensurepip): {exc}",
                file=sys.stderr,
            )
            return 2

    python = environment_python(destination)
    project_spec = str(PROJECT_ROOT)
    if args.extras:
        project_spec = f"{project_spec}[{args.extras}]"
    command = [str(python), "-m", "pip", "install", "--editable", project_spec]
    print("Installing LyricRail...")
    result = subprocess.run(command, check=False)
    if result.returncode:
        return result.returncode

    try:
        launcher = create_stable_launcher(destination)
        print(f"Stable launcher: {launcher}")
        if args.add_to_path:
            register_windows_environment(launcher.parent)
            print(f"Registered in User PATH: {launcher.parent}")
    except OSError as exc:
        print(f"Could not register launcher: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
