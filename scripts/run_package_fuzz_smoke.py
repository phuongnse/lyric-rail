from __future__ import annotations

import os
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
        fuzzer_arguments = ["-runs=1000", "-max_len=512"]
        if os.name == "nt":
            # Windows sanitizer symbolization can leave llvm-symbolizer alive after
            # a successful run. The smoke still exercises identical inputs and
            # coverage; a failing artifact can be replayed with symbolization.
            fuzzer_arguments.append("-symbolize=0")
        completed = subprocess.run(
            [
                "cargo",
                "+nightly-2026-07-01",
                "fuzz",
                "run",
                "package_open",
                str(temporary_corpus),
                "--",
                *fuzzer_arguments,
            ],
            cwd=ROOT,
            check=False,
        )
        return completed.returncode
    finally:
        shutil.rmtree(temporary_root)


if __name__ == "__main__":
    raise SystemExit(main())
