# Contributing to LyricRail

## Setup

On Windows, use the repository-owned native bootstrap. It installs the official
toolchain and all code dependencies, creates `.venv`, imports MSVC and runs the governed
profiles without WSL:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\bootstrap_windows.ps1 -Plan
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\bootstrap_windows.ps1
```

The read-only plan reports Windows Smart App Control status. Enforcement mode is not
compatible with unsigned local Rust/Tauri builds, so bootstrap fails before mutation
and directs the developer to make the Windows Security decision; it does not turn off
or bypass application control.

Bootstrap also rejects an existing non-3.12/non-x64/foreign `.venv` instead of
overlaying it. CPU and NVIDIA runtimes are transitioned as mutually exclusive sets;
the NVIDIA receipt requires live PyTorch CUDA and ONNX CUDA-provider evidence.

Use `-IncludeModels` only when the task needs the large pinned inference weights and
their licenses have been reviewed. On other platforms, use Python 3.11 or newer and
create an isolated development environment from the repository root:

```text
python scripts/install.py --extras dev
python -m pytest -q
```

Install alignment or separation extras only when the change needs those engines. FFmpeg and ffprobe must come from `PATH` or the documented environment variables.

Install the hash-locked engineering-process authority separately or into the active
environment, then validate the toolchain profile needed by the change:

```text
python -m pip install --require-hashes -r requirements/process.txt
processctl adoption check --project-root . --requirements-lock requirements/process.txt
processctl doctor --project-root . --profile python
```

The canonical verification profiles are `python`, `frontend`, and `rust`. Changes
to package format, cryptography, key management, recovery, parser, playback,
runtime signing, the privileged broker, or volume security must also include the
`security` profile in their change contract. Run a profile with:

```text
processctl verify --project-root . --profile python
```

## Change contract

- Enter every non-trivial change through the managed `run-change` skill. Keep its
  contract, plan, verification, and review evidence bound to the same clean Git
  checkpoint under `.process/runs/`.
- Independent review must use a read-only actor and fresh context that did not
  implement the current cycle. Resolve every required finding before completion.
- Keep orchestration and path handling platform-neutral. Do not add WSL-only, Windows-only, or POSIX-only behavior without a guarded implementation and tests.
- Treat the supplied UTF-8 lyrics as immutable text. Audio may align those exact words and classify roles; text detection, lyric providers, and captions are forbidden.
- Put reusable behavior in config, templates, or high-level algorithms; do not branch on song titles or lyric phrases.
- Add or update tests for scheduling, timing gates, role classification, source resolution, and job-state changes.
- Preserve user media and unrelated work files. Cleanup must stay scoped to an explicitly completed job.
- Run all required profiles from `.process/project.json` before opening a pull
  request. CI runs the Python and Rust profiles across the supported operating
  systems, builds/tests the frontend on Linux, and adds the security/fuzz gates.

## Repository hygiene

Never commit any of the following:

- source media, rendered videos, stems, previews, logs, or cache files;
- model weights or downloaded tool/runtime bundles;
- `.env`, OAuth client files, refresh tokens, API credentials, or channel-private data;
- private metadata/role sidecars copied from a production batch unless they are intentionally licensed test fixtures.

The repository should contain source, tests, documentation, configuration defaults, templates, and redistributable assets only.
