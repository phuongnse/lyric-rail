# Managed by engineering-process; do not edit.
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path


COMMAND_TIMEOUT_SECONDS = 300
TERMINATION_TIMEOUT_SECONDS = 5
MAX_CAPTURE_BYTES = 128_000
READ_CHUNK_BYTES = 64 * 1024
MAX_REQUIREMENTS_BYTES = 1_000_000
FILE_ATTRIBUTE_REPARSE_POINT = 0x400
PathIdentity = tuple[int, int, int, int]


@dataclass
class Capture:
    content: bytearray = field(default_factory=bytearray)
    count: int = 0
    digest: object = field(default_factory=hashlib.sha256)

    def add(self, chunk: bytes) -> None:
        self.count += len(chunk)
        self.digest.update(chunk)
        remaining = MAX_CAPTURE_BYTES - len(self.content)
        if remaining > 0:
            self.content.extend(chunk[:remaining])

    def text(self) -> str:
        value = bytes(self.content).decode("utf-8", errors="replace")
        if self.count > len(self.content):
            value += (
                f"\n[output truncated: {self.count} bytes, "
                f"sha256:{self.digest.hexdigest()}]\n"
            )
        return value


def _drain(stream: object, capture: Capture) -> None:
    try:
        while chunk := stream.read(READ_CHUNK_BYTES):
            capture.add(chunk)
    finally:
        stream.close()


def _process_group_exists(process_group: int) -> bool:
    try:
        os.killpg(process_group, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _wait_for_process_group(process_group: int, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _process_group_exists(process_group):
            return True
        time.sleep(0.02)
    return not _process_group_exists(process_group)


def _terminate_posix_group(process_group: int) -> bool:
    if not _process_group_exists(process_group):
        return False
    try:
        os.killpg(process_group, signal.SIGTERM)
    except ProcessLookupError:
        return True
    except PermissionError as error:
        if _wait_for_process_group(process_group, TERMINATION_TIMEOUT_SECONDS):
            return True
        raise RuntimeError(
            "command process group could not be signaled during bounded termination"
        ) from error
    if _wait_for_process_group(process_group, TERMINATION_TIMEOUT_SECONDS):
        return True
    try:
        os.killpg(process_group, signal.SIGKILL)
    except ProcessLookupError:
        return True
    except PermissionError as error:
        if _wait_for_process_group(process_group, TERMINATION_TIMEOUT_SECONDS):
            return True
        raise RuntimeError(
            "command process group could not be killed during bounded termination"
        ) from error
    if not _wait_for_process_group(process_group, TERMINATION_TIMEOUT_SECONDS):
        raise RuntimeError("command process group survived bounded termination")
    return True


def _terminate_tree(process: subprocess.Popen[bytes]) -> bool:
    if os.name == "nt":
        if process.poll() is not None:
            return False
        process.terminate()
        try:
            process.wait(timeout=TERMINATION_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=TERMINATION_TIMEOUT_SECONDS)
        return True

    if process.poll() is None:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=TERMINATION_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=TERMINATION_TIMEOUT_SECONDS)
            except subprocess.TimeoutExpired as error:
                raise RuntimeError(
                    "command root process survived bounded termination"
                ) from error
    return _terminate_posix_group(process.pid)


def _windows_wrapped_command(argv: list[str]) -> list[str]:
    supplied = Path(argv[0])
    if not supplied.is_absolute() or supplied.suffix.casefold() != ".exe":
        raise RuntimeError("Windows adoption commands require an absolute .exe path")
    try:
        application = supplied.resolve(strict=True)
    except OSError as error:
        raise RuntimeError("Windows adoption command executable is unavailable") from error
    helper = Path(__file__).resolve().with_name("adopt-process-windows-job.py")
    if helper.is_symlink() or not helper.is_file():
        raise RuntimeError("managed Windows Job Object helper is unavailable")
    return [
        sys.executable,
        "-I",
        str(helper),
        "--application",
        str(application),
        "--",
        *argv,
    ]


def _child_environment() -> dict[str, str]:
    allowed = {
        "LANG",
        "LC_ALL",
        "PATH",
        "SYSTEMROOT",
        "TEMP",
        "TMP",
        "TMPDIR",
        "WINDIR",
    }
    environment = {key: value for key, value in os.environ.items() if key in allowed}
    environment.update(
        {
            "PIP_CONFIG_FILE": os.devnull,
            "PIP_DISABLE_PIP_VERSION_CHECK": "1",
            "PIP_NO_INPUT": "1",
            "PYTHONNOUSERSITE": "1",
        }
    )
    return environment


def _is_link_or_reparse(value: os.stat_result) -> bool:
    return stat.S_ISLNK(value.st_mode) or bool(
        getattr(value, "st_file_attributes", 0)
        & FILE_ATTRIBUTE_REPARSE_POINT
    )


def _path_identity(value: os.stat_result) -> PathIdentity:
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_mtime_ns,
    )


def _same_file_identity(left: os.stat_result, right: os.stat_result) -> bool:
    """Compare stable ids when Windows path stat omits the volume serial."""
    if os.name == "nt":
        return (
            left.st_ino != 0
            and left.st_ino == right.st_ino
            and (
                left.st_dev == 0
                or right.st_dev == 0
                or left.st_dev == right.st_dev
            )
        )
    return left.st_dev == right.st_dev and left.st_ino == right.st_ino


def _path_identity_chain(
    root: Path, path: Path
) -> tuple[PathIdentity, ...]:
    try:
        relative = path.relative_to(root)
    except ValueError as error:
        raise RuntimeError(f"requirements lock escaped checkout: {path}") from error
    if not relative.parts:
        raise RuntimeError("requirements lock must name a file")
    try:
        root_value = root.lstat()
    except OSError as error:
        raise RuntimeError(
            f"cannot inspect requirements root {root}: {error}"
        ) from error
    if _is_link_or_reparse(root_value) or not stat.S_ISDIR(root_value.st_mode):
        raise RuntimeError("requirements root must be a regular directory")
    chain: list[PathIdentity] = [_path_identity(root_value)]
    current = root
    for index, part in enumerate(relative.parts):
        current /= part
        try:
            value = current.lstat()
        except OSError as error:
            raise RuntimeError(
                f"cannot inspect requirements path {current}: {error}"
            ) from error
        if _is_link_or_reparse(value):
            raise RuntimeError(
                f"requirements path must not traverse a link or reparse point: {current}"
            )
        if index < len(relative.parts) - 1 and not stat.S_ISDIR(value.st_mode):
            raise RuntimeError(
                f"requirements path ancestor must be a directory: {current}"
            )
        chain.append(_path_identity(value))
    return tuple(chain)


def _requirements_binding(
    project_root: Path, supplied: Path
) -> tuple[Path, Path]:
    requested = supplied if supplied.is_absolute() else project_root / supplied
    candidate = Path(os.path.abspath(os.fspath(requested)))
    try:
        canonical_root = project_root.resolve(strict=True)
        canonical_candidate = candidate.resolve(strict=True)
        relative = canonical_candidate.relative_to(canonical_root)
    except (OSError, ValueError) as error:
        raise RuntimeError(
            "requirements lock must be a regular file inside the checkout"
        ) from error
    if not relative.parts:
        raise RuntimeError("requirements lock must name a file inside the checkout")
    if len(relative.parts) > len(candidate.parents):
        raise RuntimeError(
            "requirements lock path does not preserve its rooted component depth"
        )
    anchor = candidate.parents[len(relative.parts) - 1]
    try:
        if anchor.resolve(strict=True) != canonical_root:
            raise RuntimeError(
                "requirements lock path does not preserve its rooted component depth"
            )
    except OSError as error:
        raise RuntimeError(
            "requirements lock must be a regular file inside the checkout"
        ) from error
    before_chain = _path_identity_chain(anchor, candidate)
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(canonical_root)
    except (OSError, ValueError) as error:
        raise RuntimeError(
            "requirements lock must be a regular file inside the checkout"
        ) from error
    if resolved != canonical_candidate:
        raise RuntimeError("requirements path changed while validating")
    if _path_identity_chain(anchor, candidate) != before_chain:
        raise RuntimeError("requirements path changed while validating")
    return candidate, anchor


def _requirements_source(project_root: Path, supplied: Path) -> Path:
    source, _anchor = _requirements_binding(project_root, supplied)
    return source


def _read_stable_requirements(
    path: Path, *, containment_root: Path | None = None
) -> bytes:
    before_chain = (
        _path_identity_chain(containment_root, path)
        if containment_root is not None
        else None
    )
    try:
        before = path.lstat()
        if not stat.S_ISREG(before.st_mode):
            raise RuntimeError("requirements lock must be a regular file")
        if before_chain is not None and _path_identity(before) != before_chain[-1]:
            raise RuntimeError("requirements path changed before opening")
        if before.st_size > MAX_REQUIREMENTS_BYTES:
            raise RuntimeError(
                f"requirements lock exceeds {MAX_REQUIREMENTS_BYTES} bytes"
            )
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        try:
            with os.fdopen(descriptor, "rb") as stream:
                descriptor = -1
                opened = os.fstat(stream.fileno())
                if (
                    not stat.S_ISREG(opened.st_mode)
                    or not _same_file_identity(opened, before)
                ):
                    raise RuntimeError("requirements lock changed while opening")
                content = stream.read(MAX_REQUIREMENTS_BYTES + 1)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
        after = path.lstat()
    except OSError as error:
        raise RuntimeError(f"cannot read requirements lock: {error}") from error
    if len(content) > MAX_REQUIREMENTS_BYTES:
        raise RuntimeError(
            f"requirements lock exceeds {MAX_REQUIREMENTS_BYTES} bytes"
        )
    if (
        len(content) != before.st_size
        or after.st_size != before.st_size
        or after.st_mtime_ns != before.st_mtime_ns
        or after.st_mode != before.st_mode
        or not _same_file_identity(after, before)
    ):
        raise RuntimeError("requirements lock changed while reading")
    if containment_root is not None:
        after_chain = _path_identity_chain(containment_root, path)
        if (
            after_chain != before_chain
            or _path_identity(after) != after_chain[-1]
        ):
            raise RuntimeError("requirements path changed while reading")
    return content


def _write_private_snapshot(path: Path, content: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    descriptor = os.open(path, flags, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = -1
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _require_unchanged(
    path: Path,
    expected: bytes,
    label: str,
    *,
    containment_root: Path | None = None,
) -> None:
    current = _read_stable_requirements(
        path, containment_root=containment_root
    )
    if current != expected:
        before = hashlib.sha256(expected).hexdigest()
        after = hashlib.sha256(current).hexdigest()
        raise RuntimeError(
            f"{label} changed during adoption: sha256:{before} -> sha256:{after}"
        )


def _run(argv: list[str], *, cwd: Path) -> str:
    command = _windows_wrapped_command(argv) if os.name == "nt" else argv
    options: dict[str, object] = {
        "cwd": cwd,
        "env": _child_environment(),
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
    }
    if os.name == "nt":
        options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        options["start_new_session"] = True
    process = subprocess.Popen(command, **options)
    stdout = Capture()
    stderr = Capture()
    stdout_thread = threading.Thread(
        target=_drain, args=(process.stdout, stdout), daemon=True
    )
    stderr_thread = threading.Thread(
        target=_drain, args=(process.stderr, stderr), daemon=True
    )
    stdout_thread.start()
    stderr_thread.start()
    try:
        return_code = process.wait(timeout=COMMAND_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired as error:
        try:
            _terminate_tree(process)
        finally:
            stdout_thread.join(timeout=TERMINATION_TIMEOUT_SECONDS)
            stderr_thread.join(timeout=TERMINATION_TIMEOUT_SECONDS)
        raise RuntimeError(
            f"command timed out after {COMMAND_TIMEOUT_SECONDS} seconds"
        ) from error
    except BaseException:
        try:
            _terminate_tree(process)
        finally:
            stdout_thread.join(timeout=TERMINATION_TIMEOUT_SECONDS)
            stderr_thread.join(timeout=TERMINATION_TIMEOUT_SECONDS)
        raise
    try:
        descendants_found = _terminate_tree(process)
    finally:
        stdout_thread.join(timeout=TERMINATION_TIMEOUT_SECONDS)
        stderr_thread.join(timeout=TERMINATION_TIMEOUT_SECONDS)
    if stdout_thread.is_alive() or stderr_thread.is_alive():
        _terminate_tree(process)
        raise RuntimeError("command output readers did not terminate")
    if descendants_found:
        raise RuntimeError("command left descendant processes; they were terminated")
    if return_code != 0:
        raise RuntimeError(
            f"command failed with exit status {return_code}\n"
            f"stdout:\n{stdout.text()}\nstderr:\n{stderr.text()}"
        )
    return stdout.text()


def _current_process_version(project_root: Path) -> str:
    lock_path = project_root / ".process" / "process.lock"
    content = _read_stable_requirements(
        lock_path, containment_root=project_root
    )
    try:
        document = json.loads(content.decode("utf-8"))
        version = document["process"]["version"]
    except (UnicodeError, json.JSONDecodeError, KeyError, TypeError) as error:
        raise RuntimeError("current process lock has no valid version") from error
    if not isinstance(version, str) or re.fullmatch(
        r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)",
        version,
    ) is None:
        raise RuntimeError("current process lock version must be final SemVer")
    return version


def _installed_process_version(python: Path, *, cwd: Path) -> str:
    version = _run(
        [
            str(python),
            "-I",
            "-c",
            "import engineering_process; print(engineering_process.VERSION)",
        ],
        cwd=cwd,
    ).strip()
    if re.fullmatch(
        r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)",
        version,
    ) is None:
        raise RuntimeError("installed process version must be final SemVer")
    return version


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Materialize one hash-locked engineering-process adoption"
    )
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--requirements-lock",
        type=Path,
        default=Path("requirements/process.txt"),
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Validate through the installed hash-locked authority without applying",
    )
    args = parser.parse_args(argv)
    project_root = Path(os.path.abspath(os.fspath(args.project_root)))
    requirements_source, requirements_root = _requirements_binding(
        project_root, args.requirements_lock
    )
    requirements_content = _read_stable_requirements(
        requirements_source, containment_root=requirements_root
    )
    requirements_digest = (
        "sha256:" + hashlib.sha256(requirements_content).hexdigest()
    )
    current_version = _current_process_version(project_root)

    with tempfile.TemporaryDirectory(
        prefix="engineering-process-adoption-"
    ) as directory:
        temporary_root = Path(directory).resolve()
        try:
            temporary_root.relative_to(project_root.resolve(strict=True))
        except ValueError:
            pass
        else:
            raise RuntimeError("temporary adoption environment must be outside checkout")
        environment_root = temporary_root / "environment"
        requirements_snapshot = temporary_root / "requirements.process.snapshot.txt"
        _write_private_snapshot(requirements_snapshot, requirements_content)
        _run(
            [sys.executable, "-I", "-m", "venv", str(environment_root)],
            cwd=project_root,
        )
        python = environment_root / (
            "Scripts/python.exe" if os.name == "nt" else "bin/python"
        )
        _run(
            [
                str(python),
                "-I",
                "-m",
                "pip",
                "install",
                "--isolated",
                "--disable-pip-version-check",
                "--no-input",
                "--require-hashes",
                "--only-binary",
                ":all:",
                "-r",
                str(requirements_snapshot),
            ],
            cwd=environment_root,
        )
        _require_unchanged(
            requirements_snapshot,
            requirements_content,
            "private requirements snapshot",
            containment_root=temporary_root,
        )
        _require_unchanged(
            requirements_source,
            requirements_content,
            "checkout requirements lock",
            containment_root=requirements_root,
        )
        target_version = _installed_process_version(
            python, cwd=environment_root
        )
        if args.check:
            output = _run(
                [
                    str(python),
                    "-I",
                    "-c",
                    "from engineering_process.cli import main; "
                    "raise SystemExit(main())",
                    "adoption",
                    "check",
                    "--project-root",
                    str(project_root),
                    "--requirements-lock",
                    str(requirements_source),
                    "--json",
                ],
                cwd=environment_root,
            )
        elif target_version == current_version:
            output = json.dumps(
                {
                    "requirementsDigest": requirements_digest,
                    "status": "unchanged",
                    "version": target_version,
                },
                sort_keys=True,
            )
        else:
            output = _run(
                [
                    str(python),
                    "-I",
                    "-m",
                    "engineering_process",
                    "adoption",
                    "apply",
                    "--project-root",
                    str(project_root),
                    "--requirements-lock",
                    str(requirements_snapshot),
                    "--requirements-source",
                    str(requirements_source),
                    "--expected-requirements-digest",
                    requirements_digest,
                    "--json",
                ],
                cwd=environment_root,
            )
        _require_unchanged(
            requirements_snapshot,
            requirements_content,
            "private requirements snapshot",
            containment_root=temporary_root,
        )
        _require_unchanged(
            requirements_source,
            requirements_content,
            "checkout requirements lock",
            containment_root=requirements_root,
        )
        try:
            result = json.loads(output)
        except (json.JSONDecodeError, UnicodeError) as error:
            raise RuntimeError("adoption authority returned invalid JSON") from error
        if (
            not isinstance(result, dict)
            or result.get("requirementsDigest") != requirements_digest
            or result.get("version") != target_version
        ):
            raise RuntimeError(
                "adoption authority did not attest the installed version and "
                "private requirements snapshot"
            )
    sys.stdout.write(output)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError) as error:
        print(f"process adoption failed: {error}", file=sys.stderr)
        raise SystemExit(2) from error
