#!/usr/bin/env python3
"""Validate the exact interpreter backing LyricRail's native Windows venv."""

from __future__ import annotations

import argparse
import json
import ntpath
from pathlib import Path
import struct
import sys
from typing import Any


def _windows_key(value: str) -> str:
    return ntpath.normcase(ntpath.normpath(value))


def collect_identity() -> dict[str, Any]:
    prefix = Path(sys.prefix)
    return {
        "version": f"{sys.version_info.major}.{sys.version_info.minor}",
        "pointerBits": struct.calcsize("P") * 8,
        "platform": sys.platform,
        "prefix": str(prefix.resolve()),
        "basePrefix": str(Path(sys.base_prefix).resolve()),
        "executable": str(Path(sys.executable).resolve()),
        "hasPyvenvConfig": (prefix / "pyvenv.cfg").is_file(),
    }


def validate_identity(identity: dict[str, Any], expected_prefix: Path) -> list[str]:
    errors: list[str] = []
    expected = expected_prefix.resolve()
    expected_executable = expected / "Scripts" / "python.exe"
    if identity.get("version") != "3.12":
        errors.append(f"expected Python 3.12, found {identity.get('version')}")
    if identity.get("pointerBits") != 64:
        errors.append(f"expected a 64-bit interpreter, found {identity.get('pointerBits')}-bit")
    if identity.get("platform") != "win32":
        errors.append(f"expected native Windows Python, found {identity.get('platform')}")
    if _windows_key(str(identity.get("prefix", ""))) != _windows_key(str(expected)):
        errors.append("interpreter prefix does not match the repository .venv")
    if _windows_key(str(identity.get("executable", ""))) != _windows_key(
        str(expected_executable)
    ):
        errors.append("interpreter is not .venv\\Scripts\\python.exe")
    if _windows_key(str(identity.get("prefix", ""))) == _windows_key(
        str(identity.get("basePrefix", ""))
    ):
        errors.append("interpreter is not running inside a virtual environment")
    if identity.get("hasPyvenvConfig") is not True:
        errors.append("repository .venv is missing pyvenv.cfg")
    return errors


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--expected-prefix", type=Path, required=True)
    options = parser.parse_args(arguments)
    identity = collect_identity()
    errors = validate_identity(identity, options.expected_prefix)
    print(
        json.dumps(
            {"schemaVersion": 1, "identity": identity, "errors": errors},
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
