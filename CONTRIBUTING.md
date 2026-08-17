# Contributing to LyricRail

## Setup

Use Python 3.11 or newer. Create an isolated development environment from the repository root:

```text
python scripts/install.py --extras dev
python -m pytest -q
```

Install the media, alignment, separation, or YouTube extras only when the change needs those engines. FFmpeg and ffprobe must come from `PATH` or the documented environment variables.

## Change contract

- Keep orchestration and path handling platform-neutral. Do not add WSL-only, Windows-only, or POSIX-only behavior without a guarded implementation and tests.
- Treat the supplied UTF-8 lyrics as immutable text. Audio may align those exact words and classify roles; text detection, lyric providers, and captions are forbidden.
- Put reusable behavior in config, templates, or high-level algorithms; do not branch on song titles or lyric phrases.
- Add or update tests for scheduling, timing gates, role classification, source resolution, and job-state changes.
- Preserve user media and unrelated work files. Cleanup must stay scoped to an explicitly completed job.
- Run `python -m pytest -q` before opening a pull request.

## Repository hygiene

Never commit any of the following:

- source media, rendered videos, stems, previews, logs, or cache files;
- model weights or downloaded tool/runtime bundles;
- `.env`, OAuth client files, refresh tokens, API credentials, or channel-private data;
- private metadata/role sidecars copied from a production batch unless they are intentionally licensed test fixtures.

The repository should contain source, tests, documentation, configuration defaults, templates, and redistributable assets only.
