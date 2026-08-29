from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SOURCE_CORPUS = ROOT / "fuzz" / "corpus" / "package_open"


def main() -> int:
    temporary_root = Path(tempfile.mkdtemp(prefix="lyric-rail-package-fuzz-"))
    temporary_corpus = temporary_root / "corpus"
    try:
        shutil.copytree(SOURCE_CORPUS, temporary_corpus)
        completed = subprocess.run(
            [
                "cargo",
                "+nightly-2026-07-01",
                "fuzz",
                "run",
                "package_open",
                str(temporary_corpus),
                "--",
                "-runs=1000",
                "-max_len=512",
            ],
            cwd=ROOT,
            check=False,
        )
        return completed.returncode
    finally:
        shutil.rmtree(temporary_root)


if __name__ == "__main__":
    raise SystemExit(main())
