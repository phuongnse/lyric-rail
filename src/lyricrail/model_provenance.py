from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any
from urllib.parse import urlsplit


_SHA256 = re.compile(r"[0-9a-f]{64}")
_MAX_MODEL_FILE_BYTES = 8 * 1024 * 1024 * 1024
_AUDIO_RELEASE_PATH = (
    "/nomadkaraoke/python-audio-separator/releases/download/model-configs/"
)
_WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    "CLOCK$",
    "CONIN$",
    "CONOUT$",
    *(f"COM{number}" for number in range(1, 10)),
    *(f"LPT{number}" for number in range(1, 10)),
}
_WINDOWS_FORBIDDEN_CHARACTERS = set('<>:"/\\|?*')


def _load_model_cache_policy() -> dict[str, Any]:
    path = Path(__file__).with_name("model_cache_policy.json")
    try:
        policy = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("Model cache policy is unavailable or invalid") from exc
    grammar = policy.get("pathGrammar") if isinstance(policy, dict) else None
    containment = policy.get("canonicalContainment") if isinstance(policy, dict) else None
    if (
        not isinstance(policy, dict)
        or policy.get("schemaVersion") != 1
        or policy.get("cacheRoot") != "models/huggingface"
        or not isinstance(grammar, dict)
        or grammar.get("separator") != "/"
        or not all(grammar.get(key) is True for key in (
            "rejectEmptySegments",
            "rejectDotSegments",
            "rejectParentSegments",
            "rejectBackslash",
            "rejectColon",
            "rejectNul",
            "rejectAbsolute",
        ))
        or not isinstance(containment, dict)
        or not all(containment.get(key) is True for key in (
            "snapshotDirectoryUnderCache",
            "targetRegularFileUnderCache",
        ))
    ):
        raise ValueError("Model cache policy does not match the supported containment grammar")
    return policy


_MODEL_CACHE_POLICY = _load_model_cache_policy()


def _nested_value(data: dict[str, Any], path: str) -> Any:
    value: Any = data
    for part in path.split("."):
        if not isinstance(value, dict) or part not in value:
            raise KeyError(path)
        value = value[part]
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(8 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _snapshot_entry(snapshot: Path, filename: str) -> Path | None:
    """Return a lexical snapshot child, rejecting cross-platform traversal."""
    grammar = _MODEL_CACHE_POLICY["pathGrammar"]
    if (
        (grammar["rejectEmptySegments"] and not filename)
        or (grammar["rejectBackslash"] and "\\" in filename)
        or (grammar["rejectColon"] and ":" in filename)
        or (grammar["rejectNul"] and "\x00" in filename)
        or (grammar["rejectEmptySegments"] and any(not part for part in filename.split("/")))
        or (grammar["rejectDotSegments"] and any(part == "." for part in filename.split("/")))
        or (grammar["rejectParentSegments"] and any(part == ".." for part in filename.split("/")))
    ):
        return None
    windows = PureWindowsPath(filename)
    posix = PurePosixPath(filename)
    if (
        windows.drive
        or windows.root
        or posix.is_absolute()
        or any(part == ".." for part in windows.parts)
        or any(part == ".." for part in posix.parts)
    ):
        return None
    candidate = snapshot / Path(filename)
    try:
        candidate.relative_to(snapshot)
    except ValueError:
        return None
    return candidate


def _contained_snapshot_directory(cache_root: Path, snapshot: Path) -> Path | None:
    containment = _MODEL_CACHE_POLICY["canonicalContainment"]
    if not containment["snapshotDirectoryUnderCache"]:
        return None
    try:
        canonical_cache = cache_root.resolve(strict=True)
        canonical_snapshot = snapshot.resolve(strict=True)
        canonical_snapshot.relative_to(canonical_cache)
    except (OSError, RuntimeError, ValueError):
        return None
    return canonical_snapshot if canonical_snapshot.is_dir() else None


def _contained_snapshot_file(
    cache_root: Path, snapshot: Path, path: Path
) -> Path | None:
    containment = _MODEL_CACHE_POLICY["canonicalContainment"]
    if (
        not containment["snapshotDirectoryUnderCache"]
        or not containment["targetRegularFileUnderCache"]
        or _contained_snapshot_directory(cache_root, snapshot) is None
    ):
        return None
    try:
        path.relative_to(snapshot)
        canonical_cache = cache_root.resolve(strict=True)
        canonical_path = path.resolve(strict=True)
        canonical_path.relative_to(canonical_cache)
    except (OSError, RuntimeError, ValueError):
        return None
    return canonical_path if canonical_path.is_file() else None


def valid_pinned_audio_filename(value: str) -> bool:
    if (
        not value
        or value in {".", ".."}
        or len(value.encode("utf-8")) > 240
        or value.endswith((" ", "."))
        or any(
            character in _WINDOWS_FORBIDDEN_CHARACTERS
            or ord(character) < 32
            or ord(character) == 127
            for character in value
        )
    ):
        return False
    path = PureWindowsPath(value)
    stem = value.split(".", 1)[0].upper()
    return (
        not path.drive
        and not path.root
        and len(path.parts) == 1
        and path.name == value
        and stem not in _WINDOWS_RESERVED_NAMES
    )


def valid_pinned_audio_url(value: str, filename: str) -> bool:
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme == "https"
        and parsed.hostname == "github.com"
        and port is None
        and parsed.username is None
        and parsed.password is None
        and not parsed.query
        and not parsed.fragment
        and parsed.path == _AUDIO_RELEASE_PATH + filename
    )


def _validate_audio_downloads(
    key: str,
    model: dict[str, Any],
    expected_files: set[str],
    errors: list[str],
) -> bool:
    urls = model.get("downloadUrls")
    sizes = model.get("fileSizeBytes")
    valid = True
    if not isinstance(urls, dict) or set(map(str, urls)) != expected_files:
        errors.append(
            f"Manifest model {key!r} downloadUrls must cover exactly {sorted(expected_files)}"
        )
        valid = False
        urls = {}
    if not isinstance(sizes, dict) or set(map(str, sizes)) != expected_files:
        errors.append(
            f"Manifest model {key!r} fileSizeBytes must cover exactly {sorted(expected_files)}"
        )
        valid = False
        sizes = {}
    for filename in sorted(expected_files):
        if not valid_pinned_audio_filename(filename):
            errors.append(f"Manifest model {key!r} has unsafe download filename {filename!r}")
            valid = False
        url = str(urls.get(filename, ""))
        if not valid_pinned_audio_url(url, filename):
            errors.append(
                f"Manifest model {key!r} has an invalid pinned HTTPS URL for {filename!r}"
            )
            valid = False
        size = sizes.get(filename)
        if isinstance(size, bool) or not isinstance(size, int) or not (0 < size <= _MAX_MODEL_FILE_BYTES):
            errors.append(
                f"Manifest model {key!r} has an invalid byte size for {filename!r}"
            )
            valid = False
    return valid


def load_model_manifest(root: Path) -> dict[str, Any]:
    path = root / "config" / "model-manifest.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"Model manifest not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Invalid model manifest at line {exc.lineno}, column {exc.colno}"
        ) from exc
    if not isinstance(data, dict) or data.get("schemaVersion") != 1:
        raise ValueError("Model manifest must be an object with schemaVersion=1")
    if not isinstance(data.get("models"), dict) or not data["models"]:
        raise ValueError("Model manifest must declare at least one model")
    return data


def verify_model_provenance(
    root: Path,
    pipeline: dict[str, Any],
    *,
    require_files: bool,
    verify_hashes: bool,
) -> dict[str, Any]:
    """Validate every active model against an immutable manifest entry."""
    manifest = load_model_manifest(root)
    checks: list[dict[str, Any]] = []
    errors: list[str] = []
    for key, model in manifest["models"].items():
        if not isinstance(model, dict):
            errors.append(f"Manifest model {key!r} is not an object")
            continue
        kind = str(model.get("type", ""))
        configured = True
        for config_path in model.get("configPaths", []):
            try:
                actual = str(_nested_value(pipeline, str(config_path)))
            except KeyError:
                errors.append(f"Missing configured model field: {config_path}")
                configured = False
                continue
            if actual != str(model.get("filename", "")):
                errors.append(
                    f"{config_path}={actual!r} is not pinned to manifest model {key!r}"
                )
                configured = False
        if kind == "huggingface-snapshot":
            for manifest_field, path_field in (
                ("repository", "repositoryConfigPath"),
                ("revision", "revisionConfigPath"),
            ):
                config_path = str(model.get(path_field, ""))
                try:
                    actual = str(_nested_value(pipeline, config_path))
                except KeyError:
                    errors.append(f"Missing configured model field: {config_path}")
                    configured = False
                    continue
                if actual != str(model.get(manifest_field, "")):
                    errors.append(
                        f"{config_path}={actual!r} does not match manifest {key!r}"
                    )
                    configured = False
            revision = str(model.get("revision", ""))
            if not re.fullmatch(r"[0-9a-f]{40}", revision):
                errors.append(f"Manifest model {key!r} has no exact 40-character revision")
                configured = False
            repository = str(model.get("repository", ""))
            cache_root = root / _MODEL_CACHE_POLICY["cacheRoot"]
            path = (
                cache_root
                / ("models--" + repository.replace("/", "--"))
                / "snapshots"
                / revision
            )
            snapshot_directory = _contained_snapshot_directory(cache_root, path)

            def resolve_snapshot_file(filename: object) -> Path | None:
                entry = _snapshot_entry(path, str(filename))
                return (
                    _contained_snapshot_file(cache_root, path, entry)
                    if entry is not None
                    else None
                )

            missing = [
                name
                for name in model.get("requiredFiles", [])
                if resolve_snapshot_file(name) is None
            ]
            present = snapshot_directory is not None and not missing
            file_hashes: dict[str, dict[str, Any]] = {}
            for filename, expected_value in model.get("fileSha256", {}).items():
                expected = str(expected_value).lower()
                if not _SHA256.fullmatch(expected):
                    errors.append(
                        f"Manifest model {key!r} has an invalid SHA-256 for {filename}"
                    )
                    configured = False
                    continue
                file_path = resolve_snapshot_file(filename)
                actual = _sha256(file_path) if verify_hashes and file_path is not None else ""
                matches = bool(actual and actual == expected) if verify_hashes else None
                if verify_hashes:
                    if file_path is None:
                        errors.append(
                            f"Snapshot hash target is missing or outside the cache for "
                            f"{key!r}/{filename}"
                        )
                    elif not matches:
                        errors.append(
                            f"Snapshot hash mismatch for {key!r}/{filename}: "
                            f"expected {expected}, got {actual}"
                        )
                file_hashes[str(filename)] = {
                    "expectedSha256": expected,
                    "actualSha256": actual or None,
                    "matches": matches,
                }
            if require_files and not present:
                errors.append(
                    f"Pinned Hugging Face snapshot is incomplete for {key!r}: {path}; "
                    f"missing={missing}"
                )
            checks.append(
                {
                    "model": key,
                    "type": kind,
                    "configured": configured,
                    "present": present,
                    "verified": configured
                    and (present or not require_files)
                    and all(
                        item["matches"] is not False for item in file_hashes.values()
                    ),
                    "path": str(path),
                    "revision": revision,
                    "missingFiles": missing,
                    "fileHashes": file_hashes,
                }
            )
            continue
        if kind == "audio-separator-checkpoint":
            filename = str(model.get("filename", ""))
            expected = str(model.get("sha256", "")).lower()
            if not _SHA256.fullmatch(expected):
                errors.append(f"Manifest model {key!r} has an invalid SHA-256")
                configured = False
            associated_manifest = model.get("associatedFileSha256", {})
            if not isinstance(associated_manifest, dict):
                errors.append(f"Manifest model {key!r} associatedFileSha256 must be an object")
                associated_manifest = {}
                configured = False
            expected_files = {filename, *map(str, associated_manifest)}
            downloads_pinned = _validate_audio_downloads(
                key, model, expected_files, errors
            )
            configured = configured and downloads_pinned
            safe_filename = (
                filename if valid_pinned_audio_filename(filename) else "invalid-model-name"
            )
            path = root / "models" / "audio-separator" / safe_filename
            present = path.is_file()
            actual = _sha256(path) if present and verify_hashes else ""
            hash_matches = bool(actual and actual == expected) if verify_hashes else None
            associated_hashes: dict[str, dict[str, Any]] = {}
            for filename, associated_expected_value in associated_manifest.items():
                filename = str(filename)
                associated_expected = str(associated_expected_value).lower()
                safe_associated = (
                    filename
                    if valid_pinned_audio_filename(filename)
                    else "invalid-associated-name"
                )
                associated_path = path.parent / safe_associated
                associated_present = associated_path.is_file()
                associated_actual = (
                    _sha256(associated_path)
                    if verify_hashes and associated_present
                    else ""
                )
                associated_matches = (
                    bool(associated_actual and associated_actual == associated_expected)
                    if verify_hashes
                    else None
                )
                if not _SHA256.fullmatch(associated_expected):
                    errors.append(
                        f"Manifest model {key!r} has an invalid SHA-256 for {filename}"
                    )
                    configured = False
                if not associated_present and (require_files or verify_hashes):
                    errors.append(
                        f"Pinned model configuration is missing for {key!r}: "
                        f"{associated_path}"
                    )
                elif verify_hashes and not associated_matches:
                    errors.append(
                        f"Model configuration hash mismatch for {key!r}/{filename}: "
                        f"expected {associated_expected}, got {associated_actual}"
                    )
                associated_hashes[filename] = {
                    "present": associated_present,
                    "expectedSha256": associated_expected,
                    "actualSha256": associated_actual or None,
                    "matches": associated_matches,
                }
            if not present and (require_files or verify_hashes):
                errors.append(f"Pinned checkpoint is missing for {key!r}: {path}")
            elif verify_hashes and not hash_matches:
                errors.append(
                    f"Checkpoint hash mismatch for {key!r}: expected {expected}, got {actual}"
                )
            checks.append(
                {
                    "model": key,
                    "type": kind,
                    "configured": configured,
                    "present": present,
                    "verified": configured
                    and (present or not require_files)
                    and (hash_matches is not False)
                    and all(
                        (item["present"] or not require_files)
                        and item["matches"] is not False
                        for item in associated_hashes.values()
                    ),
                    "path": str(path),
                    "expectedSha256": expected,
                    "actualSha256": actual or None,
                    "associatedFileHashes": associated_hashes,
                    "downloadsPinned": downloads_pinned,
                }
            )
            continue
        errors.append(f"Manifest model {key!r} uses unsupported type {kind!r}")
    return {
        "kind": "lyricrail.model-provenance",
        "policy": str(manifest.get("policy", "")),
        "valid": not errors and all(check["verified"] is True for check in checks),
        "errors": errors,
        "checks": checks,
    }


def assert_model_provenance(
    root: Path, pipeline: dict[str, Any], *, verify_hashes: bool = True
) -> dict[str, Any]:
    report = verify_model_provenance(
        root, pipeline, require_files=True, verify_hashes=verify_hashes
    )
    if not report["valid"]:
        raise ValueError("Model provenance gate failed: " + "; ".join(report["errors"]))
    return report
