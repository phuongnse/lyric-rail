# LyricRail CLI contract

## Commands

```text
lyricrail doctor [--production] [--json]
lyricrail config [validate|show] [--json]
lyricrail plan [SOURCE] --lyrics PATH [--start TIME] [--end TIME] [--title TITLE] [--artist ARTIST] [--upload|--no-upload] [--json]
lyricrail run [SOURCE] --lyrics PATH [--start TIME] [--end TIME] [--title TITLE] [--artist ARTIST] [--upload|--no-upload] [--dry-run] [--json]
lyricrail jobs [--status STATUS] [--limit N] [--json]
lyricrail status [JOB_ID|latest] [--watch] [--json]
lyricrail logs [JOB_ID|latest] [--stage STAGE] [--tail N] [--follow]
lyricrail events [JOB_ID|latest] [--tail N] [--follow]
lyricrail cancel [JOB_ID|latest] [--json]
lyricrail retry [JOB_ID|latest] [--from-stage STAGE] [--run] [--json]
lyricrail preview [JOB_ID|latest] [--start SEC] [--duration SEC] [--json]
```

`SOURCE` is one YouTube/web URL or a local audio/video path. If omitted, `input\` must contain exactly one supported media file. Every command accepts `--root PATH`. When omitted, LyricRail checks `LYRICRAIL_HOME`, the current directory, and finally the source checkout.

`--lyrics` is mandatory for `plan` and `run`. It must point to a UTF-8 plain-text file containing the exact lyrics, one semantic phrase per non-empty line. The file is snapshotted into the job before processing. No model, caption, or online source may add, remove, or replace lyric words.

`--start` and `--end` refer to the original source timeline and accept seconds, `MM:SS`, or `HH:MM:SS`. `--title` and `--artist` are recommended when selecting one song from a compilation so filenames and YouTube metadata describe the selected song rather than the source program.

## Recommended workflow

```text
lyricrail config validate
lyricrail doctor
lyricrail doctor --production
lyricrail plan "/path/to/Song - Artist.mp4" --lyrics "/path/to/lyrics.txt" --no-upload
lyricrail run "/path/to/Song - Artist.flac" --lyrics "/path/to/lyrics.txt" --no-upload
lyricrail run "https://www.youtube.com/watch?v=VIDEO_ID" --lyrics "/path/to/lyrics.txt" --upload
lyricrail run "https://www.youtube.com/watch?v=VIDEO_ID" --lyrics "/path/to/lyrics.txt" --start 03:05 --end 06:32 --title "Song" --artist "Artist" --upload
lyricrail status latest --watch
lyricrail logs latest --follow
```

`plan` and `run --dry-run` do not create a job or write media output. `run` creates a durable job before starting the first stage.

`doctor --production` additionally requires every configured model snapshot and
checkpoint to match `config/model-manifest.json`, including full SHA-256 hashing
of audio checkpoints. `run` applies the same model provenance gate before it
downloads or processes the requested source.

Use `preview` to render up to 120 seconds with the same karaoke scheduler and subtitle renderer as the final output:

```text
lyricrail preview latest --start 60 --duration 12
```

## Job status

| Status | Meaning |
|---|---|
| `queued` | The job is durable and waiting for the runner |
| `running` | At least one stage is active |
| `cancelling` | Cooperative cancellation was requested |
| `succeeded` | Every active stage completed |
| `failed` | An engine or quality gate failed |
| `blocked` | A required engine, credential, or external condition is missing |
| `cancelled` | The job stopped by request |

Stages additionally use `pending` and `skipped`. Skipped YouTube stages do not affect total progress.

## Human and machine output

The default output is optimized for terminals. `--json` emits UTF-8 JSON with stable discriminators:

- `lyricrail.validation`
- `lyricrail.plan`
- `lyricrail.job`
- `lyricrail.job-list`
- `lyricrail.preview`
- `lyricrail.error`

`events.jsonl` is append-only NDJSON. Automation must use fields and schema versions rather than parsing terminal tables. `status --watch --json` emits one compact JSON object per line.

## Karaoke render artifacts

The subtitle stage publishes two auditable artifacts before the final video is rendered:

- `karaoke-render-plan.json`: display schedule, visual timing, metrics, warnings, and quality-gate results.
- `karaoke.ass`: renderer input generated from the validated plan.

An invalid render plan stops the job. It is never downgraded to a warning during production rendering.

For audio-only sources, `landscape-license-manifest.json` records the content-derived searches, selected stock pages, direct asset URLs, timing, and Mixkit license URL. Landscape clips are downloaded per job; the repository contains no reusable fixed clip set.

## Exit codes

| Code | Meaning |
|---:|---|
| `0` | Success |
| `1` | `doctor` found an incomplete runtime |
| `2` | Input, configuration, or CLI error |
| `3` | Job blocked |
| `4` | Job failed |
| `130` | Cancelled or interrupted with Ctrl+C |

## Durability and retries

- `job.json` is written to a same-volume temporary file, flushed, and atomically replaced.
- `events.jsonl` and stage logs are append-only.
- Job updates use an exclusive lock; stale locks are recoverable.
- Invalid job and stage transitions are rejected.
- Engine exceptions become structured errors with stage, retryability, and traceback logs.
- `retry` preserves successful stages and resets the selected stage and downstream stages.
- Every `run` creates a fresh, isolated job. Source/model caches may be shared, but
  stems, lyric alignment, role decisions, and render artifacts are never read from
  another job.
- Long-running handlers call cancellation checkpoints.

## Cleanup contract

Failed, blocked, and cancelled jobs retain their work files. For app packaging,
automatic cleanup runs only after the final `.lrail` package passes a second
native full verification. It removes only the current job's `work`, `inputs`,
and `artifacts` directories, retains logs/manifests, and never removes source
media, shared caches, or another job. Cleanup is ordinary filesystem deletion;
it does not claim secure erasure on SSDs.

The older YouTube path remains disabled by default. If explicitly enabled,
upload cleanup is separately gated on a confirmed non-empty video ID.
