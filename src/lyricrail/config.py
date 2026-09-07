from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


SOURCE_PROJECT_ROOT = Path(__file__).resolve().parents[2]


def load_dotenv(path: Path, override: bool = False) -> int:
    """Load a conservative KEY=VALUE file without adding a runtime dependency."""
    if not path.is_file():
        return 0
    loaded = 0
    for line_number, raw_line in enumerate(
        path.read_text(encoding="utf-8-sig").splitlines(), start=1
    ):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            raise ValueError(f"Invalid .env entry at {path}:{line_number}")
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key or not key.replace("_", "A").isalnum() or key[0].isdigit():
            raise ValueError(f"Invalid .env variable name at {path}:{line_number}")
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        if override or key not in os.environ:
            os.environ[key] = value
            loaded += 1
    return loaded


def load_project_environment(candidate_root: Path | None = None) -> Path:
    """Load cwd/root .env files and return the resolved project root."""
    load_dotenv(Path.cwd() / ".env")
    root = resolve_project_root(candidate_root)
    if root != Path.cwd().resolve():
        load_dotenv(root / ".env")
    return root


def resolve_environment_path(name: str, root: Path, default: str) -> Path:
    raw = os.environ.get(name, default).strip() or default
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = root / path
    return path.resolve()


def resolve_data_root(project_root: Path) -> Path:
    """Resolve mutable user data independently from the immutable runtime."""
    configured = os.environ.get("LYRICRAIL_DATA_HOME", "").strip()
    if not configured:
        return project_root.resolve()
    path = Path(configured).expanduser()
    if not path.is_absolute():
        raise ValueError("LYRICRAIL_DATA_HOME must be an absolute path")
    return path.resolve()


def resolve_project_root(explicit: Path | None = None) -> Path:
    """Resolve the data project independently from the installed Python package."""
    if explicit is not None:
        return explicit.expanduser().resolve()

    configured = os.environ.get("LYRICRAIL_HOME", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()

    current = Path.cwd().resolve()
    if (current / "config" / "pipeline.json").is_file():
        return current

    if (SOURCE_PROJECT_ROOT / "config" / "pipeline.json").is_file():
        return SOURCE_PROJECT_ROOT

    raise ValueError(
        "Unable to determine the project directory. Run inside LyricRail, "
        "use --root, or set LYRICRAIL_HOME."
    )


def load_json(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError as exc:
        raise ValueError(f"Configuration file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Invalid JSON in {path} at line {exc.lineno}, column {exc.colno}"
        ) from exc

    if not isinstance(data, dict):
        raise ValueError(f"Configuration must be a JSON object: {path}")
    return data


def load_project_config(root: Path) -> dict[str, dict[str, Any]]:
    config_dir = root / "config"
    return {
        "pipeline": load_json(config_dir / "pipeline.json"),
    }
