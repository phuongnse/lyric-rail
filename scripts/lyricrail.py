#!/usr/bin/env python3
"""Portable source-checkout entry point for Windows, macOS, and Linux."""

from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
SOURCE_ROOT = PROJECT_ROOT / "src"
sys.path.insert(0, str(SOURCE_ROOT))

from lyricrail.__main__ import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
