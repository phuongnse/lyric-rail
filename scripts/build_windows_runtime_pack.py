from __future__ import annotations

import argparse
import json
import os
import shutil
import stat
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any


VERSION = "0.8.0"
PLATFORM = "windows-x86_64"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Assemble, production-audit, sign, and verify a relocatable Windows "
            "LyricRail local-core runtime pack."
        )
    )
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--private-key", type=Path, required=True)
    parser.add_argument("--public-key", type=Path, required=True)
    parser.add_argument(
        "--lrail",
        type=Path,
        default=None,
        help="Native lrail executable; defaults to target/release/lrail.exe",
    )
    return parser


def _is_reparse(path: Path) -> bool:
    info = path.lstat()
    attributes = getattr(info, "st_file_attributes", 0)
    return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))


def _copy_tree_materialized(
    source: Path,
    destination: Path,
    *,
    skip_names: frozenset[str] = frozenset(),
    skip_prefixes: tuple[str, ...] = (),
) -> None:
    if not source.is_dir():
        raise RuntimeError(f"Required directory is missing: {source}")
    destination.mkdir(parents=True, exist_ok=False)
    for entry in os.scandir(source):
        source_path = Path(entry.path)
        name = entry.name
        if name in skip_names or name.startswith(skip_prefixes):
            continue
        if entry.is_symlink() or _is_reparse(source_path):
            raise RuntimeError(f"Runtime source contains a symlink/reparse point: {source_path}")
        destination_path = destination / name
        if entry.is_dir(follow_symlinks=False):
            _copy_tree_materialized(
                source_path,
                destination_path,
                skip_names=skip_names,
                skip_prefixes=skip_prefixes,
            )
        elif entry.is_file(follow_symlinks=False):
            shutil.copy2(source_path, destination_path)
        else:
            raise RuntimeError(f"Runtime source contains a special file: {source_path}")


def _copy_file(source: Path, destination: Path) -> None:
    if not source.is_file() or source.is_symlink() or _is_reparse(source):
        raise RuntimeError(f"Required regular file is missing or unsafe: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise RuntimeError(f"Runtime staging collision: {destination}")
    shutil.copy2(source, destination)


def _remove_tree(path: Path) -> None:
    """Remove a private staging tree, including read-only vendor files."""

    def make_writable_and_retry(
        function: Any,
        failing_path: str,
        _error: tuple[type[BaseException], BaseException, Any],
    ) -> None:
        os.chmod(failing_path, stat.S_IWRITE | stat.S_IREAD)
        function(failing_path)

    shutil.rmtree(path, onerror=make_writable_and_retry)


def _run(
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        command,
        cwd=cwd,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode != 0:
        rendered = subprocess.list2cmdline(command)
        raise RuntimeError(
            f"Command failed ({result.returncode}): {rendered}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


def _inventory(root: Path) -> tuple[int, int]:
    count = 0
    size = 0
    for current, directories, files in os.walk(root):
        current_path = Path(current)
        for name in directories:
            path = current_path / name
            if path.is_symlink() or _is_reparse(path):
                raise RuntimeError(f"Staged runtime contains a reparse point: {path}")
        for name in files:
            path = current_path / name
            if path.is_symlink() or _is_reparse(path) or not path.is_file():
                raise RuntimeError(f"Staged runtime contains an unsafe file: {path}")
            count += 1
            size += path.stat().st_size
    return count, size


def _assemble(root: Path, staging: Path, lrail: Path) -> None:
    python_root = staging / "runtime" / "python"
    _copy_tree_materialized(root / "runtime" / "python312", python_root)

    site_packages = python_root / "Lib" / "site-packages"
    if site_packages.exists():
        shutil.rmtree(site_packages)
    _copy_tree_materialized(
        root / ".venv" / "Lib" / "site-packages",
        site_packages,
        skip_names=frozenset({"lyricrail-0.8.0.dist-info"}),
        skip_prefixes=("__editable__",),
    )
    lyricrail_package = site_packages / "lyricrail"
    if lyricrail_package.exists():
        raise RuntimeError(f"Unexpected pre-existing LyricRail package: {lyricrail_package}")
    _copy_tree_materialized(root / "src" / "lyricrail", lyricrail_package)

    for name in ("models", "config", "assets", "templates"):
        _copy_tree_materialized(root / name, staging / name)
    # Runtime inference imports only this model definition. Training scripts,
    # notebooks, caches, and VCS metadata are intentionally excluded from the
    # signed pack to reduce both size and executable attack surface. Retain the
    # upstream license alongside the vendored source.
    _copy_file(
        root / "vendor" / "lyric-alignment" / "model_handling.py",
        staging / "vendor" / "lyric-alignment" / "model_handling.py",
    )
    _copy_file(
        root / "vendor" / "lyric-alignment" / "LICENSE",
        staging / "vendor" / "lyric-alignment" / "LICENSE",
    )
    _copy_file(root / "tools" / "ffmpeg" / "bin" / "ffmpeg.exe", staging / "tools" / "ffmpeg" / "bin" / "ffmpeg.exe")
    _copy_file(root / "tools" / "ffmpeg" / "bin" / "ffprobe.exe", staging / "tools" / "ffmpeg" / "bin" / "ffprobe.exe")
    _copy_file(lrail, staging / "bin" / "lrail.exe")


def _doctor(staging: Path, data_root: Path) -> dict[str, Any]:
    environment = _runtime_environment(staging, data_root)
    python = staging / "runtime" / "python" / "python.exe"
    result = _run(
        [
            str(python),
            "-I",
            "-m",
            "lyricrail",
            "doctor",
            "--root",
            str(staging),
            "--production",
            "--json",
        ],
        cwd=staging,
        env=environment,
    )
    report = json.loads(result.stdout)
    if report.get("ready") is not True:
        raise RuntimeError("Production doctor did not declare the staged runtime ready")
    return report


def _runtime_environment(staging: Path, data_root: Path) -> dict[str, str]:
    python = staging / "runtime" / "python" / "python.exe"
    ffmpeg = staging / "tools" / "ffmpeg" / "bin" / "ffmpeg.exe"
    ffprobe = staging / "tools" / "ffmpeg" / "bin" / "ffprobe.exe"
    lrail = staging / "bin" / "lrail.exe"
    if not data_root.exists():
        data_root.mkdir(parents=True, exist_ok=False)
    # The immutable runtime and mutable job workspace are deliberately split.
    # Production validation requires the complete workspace contract even for
    # an empty first run; create it outside the signed runtime root.
    for directory in ("input", "output", "cache", "logs", "credentials"):
        (data_root / directory).mkdir(exist_ok=True)
    environment = os.environ.copy()
    environment.update(
        {
            "LYRICRAIL_HOME": str(staging),
            "LYRICRAIL_DATA_HOME": str(data_root),
            "LYRICRAIL_FFMPEG": str(ffmpeg),
            "LYRICRAIL_FFPROBE": str(ffprobe),
            "LYRICRAIL_LRAIL": str(lrail),
            "PYTHONNOUSERSITE": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONUNBUFFERED": "1",
        }
    )
    if not python.is_file():
        raise RuntimeError(f"Staged Python is missing: {python}")
    return environment


def _smoke_models(staging: Path, data_root: Path, script: Path) -> str:
    environment = _runtime_environment(staging, data_root)
    python = staging / "runtime" / "python" / "python.exe"
    result = _run(
        [str(python), "-I", str(script)],
        cwd=staging,
        env=environment,
    )
    return result.stdout.strip()


def build(args: argparse.Namespace) -> Path:
    root = args.root.resolve(strict=True)
    output = args.output.resolve(strict=False)
    private_key = args.private_key.resolve(strict=True)
    public_key = args.public_key.resolve(strict=True)
    lrail = (args.lrail or (root / "target" / "release" / "lrail.exe")).resolve(strict=True)

    if output.exists():
        raise RuntimeError(f"Output already exists; refusing to overwrite: {output}")
    copied_roots = (
        root / ".venv",
        root / "runtime" / "python312",
        root / "models",
        root / "config",
        root / "assets",
        root / "templates",
        root / "tools",
    )
    if any(source == output or source in output.parents for source in copied_roots):
        raise RuntimeError("Output must be outside every source tree copied into the pack")
    if private_key == output or output in private_key.parents:
        raise RuntimeError("Runtime signing private key must remain outside the runtime pack")
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = output.parent / f".{output.name}.staging-{uuid.uuid4().hex}"
    data_root = output.parent / f".{output.name}.doctor-data-{uuid.uuid4().hex}"
    report_path = output.parent / f"{output.name}.build-report.json"
    if report_path.exists():
        raise RuntimeError(f"Build report already exists; refusing to overwrite: {report_path}")

    try:
        staging.mkdir()
        _assemble(root, staging, lrail)
        doctor = _doctor(staging, data_root)
        model_smoke = _smoke_models(staging, data_root, root / "scripts" / "smoke_models.py")
        # No mutable doctor/model cache may survive publication. Removing it
        # before signing also ensures a cleanup failure cannot leave a pack
        # that appears successfully published.
        _remove_tree(data_root)
        before_count, before_size = _inventory(staging)
        manifest = _run(
            [
                str(lrail),
                "runtime-manifest",
                "--root",
                str(staging),
                "--python",
                "runtime/python/python.exe",
                "--ffmpeg",
                "tools/ffmpeg/bin/ffmpeg.exe",
                "--ffprobe",
                "tools/ffmpeg/bin/ffprobe.exe",
                "--lrail",
                "bin/lrail.exe",
                "--private-key",
                str(private_key),
            ],
            cwd=root,
        )
        verification = _run(
            [
                str(lrail),
                "runtime-verify",
                "--root",
                str(staging),
                "--public-key",
                str(public_key),
            ],
            cwd=root,
        )
        after_count, after_size = _inventory(staging)
        staging.rename(output)
        report = {
            "schemaVersion": 1,
            "lyricRailVersion": VERSION,
            "platform": PLATFORM,
            "output": str(output),
            "filesBeforeManifest": before_count,
            "bytesBeforeManifest": before_size,
            "filesAfterManifest": after_count,
            "bytesAfterManifest": after_size,
            "doctor": doctor,
            "modelSmoke": model_smoke,
            "manifestCommand": manifest.stdout.strip(),
            "verificationCommand": verification.stdout.strip(),
            "distribution": (
                "Private local release-candidate runtime. Model/checkpoint licenses "
                "must be reviewed before redistribution."
            ),
        }
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        return output
    except BaseException as error:
        cleanup_failures: list[str] = []
        for temporary in (staging, data_root):
            if not temporary.exists():
                continue
            try:
                _remove_tree(temporary)
            except OSError as cleanup_error:
                cleanup_failures.append(f"{temporary}: {cleanup_error}")
        if cleanup_failures:
            error.add_note(
                "Runtime builder could not fully remove private staging data: "
                + "; ".join(cleanup_failures)
            )
        raise


def main() -> int:
    try:
        output = build(_parser().parse_args())
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
