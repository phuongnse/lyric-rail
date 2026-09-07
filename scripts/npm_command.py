#!/usr/bin/env python3
"""Run the installed npm CLI through the native Node executable without a shell."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path


def resolve_node_and_npm_cli() -> tuple[Path, Path]:
    node_command = shutil.which("node")
    npm_command = shutil.which("npm")
    if not node_command or not npm_command:
        raise RuntimeError(
            "Node.js and npm are required. On Windows run scripts/bootstrap_windows.ps1."
        )
    node = Path(node_command).resolve(strict=True)
    npm = Path(npm_command).resolve(strict=True)
    candidates = [
        npm if npm.name == "npm-cli.js" else None,
        node.parent / "node_modules" / "npm" / "bin" / "npm-cli.js",
        npm.parent / "node_modules" / "npm" / "bin" / "npm-cli.js",
        npm.parent.parent / "lib" / "node_modules" / "npm" / "bin" / "npm-cli.js",
    ]
    cli = next(
        (
            candidate.resolve()
            for candidate in candidates
            if candidate is not None and candidate.is_file()
        ),
        None,
    )
    if cli is None:
        raise RuntimeError(f"Unable to locate npm-cli.js beside {node} or {npm}")
    return node, cli


def main(arguments: list[str] | None = None) -> int:
    forwarded = list(sys.argv[1:] if arguments is None else arguments)
    if not forwarded:
        print("npm arguments are required", file=sys.stderr)
        return 2
    try:
        node, npm_cli = resolve_node_and_npm_cli()
    except (OSError, RuntimeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2
    return subprocess.run([str(node), str(npm_cli), *forwarded], check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
