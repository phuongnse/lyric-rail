# LyricRail local core CLI

The production pipeline accepts regular local media only. Cloud objects and remote
locators never enter these commands. The Player's Clip Editor passes the selected native
path and optional Start/End values to this same worker without modifying the source.

```text
lyricrail doctor [--production] [--json]
lyricrail config [validate|show] [--json]
lyricrail plan [SOURCE] --lyrics PATH [--start TIME] [--end TIME]
               [--title TITLE] [--artist ARTIST] [--composer COMPOSER] [--json]
lyricrail run [SOURCE] --lyrics PATH [--start TIME] [--end TIME]
              [--title TITLE] [--artist ARTIST] [--composer COMPOSER]
              [--dry-run] [--json]
lyricrail worker [--root PATH]
lyricrail revision-align --audio FILE --timing JSON --lyrics TXT --output JSON [--root PATH]
lyricrail jobs|status|logs|events|cancel|retry|preview ...
lrail verify-request song.lrail --request package-request.json
```

`--lyrics` is mandatory UTF-8 text with one semantic phrase per non-empty line.
The job snapshots it before processing. Models align the supplied text and classify
roles; they do not silently replace its words. Player-confirmed corrections create a
new authoritative revision instead of modifying the source file.

`worker` is the native Player's bounded JSON-lines interface. It loads and verifies the
runtime once, processes requests sequentially, retains safe model caches in the worker
process, emits weighted job/stage progress no faster than five times per second, streams
structured stdout/stderr/progress output while tools run, and keeps every job's clear
artifacts isolated. Subprocess pipes are drained concurrently with bounded lines and
capture buffers; executable and argument arrays are recorded separately rather than
reconstructed as a shell command. FFmpeg uses `-progress pipe:1` only when the output
duration is declared; otherwise the stage remains honestly indeterminate. It is not a
network service.

`revision-align` is an internal native-backend operation. It consumes exact confirmed
text, packaged Original audio and existing timing, acoustically re-aligns only changed
safe lines, and rejects unsafe word/line structure. It never repeats source separation
or media encoding.

Job state and retry semantics remain durable: manifests are atomically replaced,
events are append-only, successful stages survive retry, and cancellation uses explicit
checkpoints. A kernel-backed per-job run lease distinguishes a crashed worker from a
still-live owner before an interrupted `running` manifest is resumed. Optional cleanup
runs only after the final `.lrail` passes a second native
full verification and never removes source media, shared model caches or another job.
Pipeline/stage logs redact private absolute paths, remote query-bearing addresses and
credential-shaped values, cap individual lines, and rotate at fixed byte ceilings.

`lrail verify-request` is the package-stage recovery boundary. It authenticates the
manifest and every encrypted chunk, compares metadata/producer/minimum-player version,
and streams every current request asset to match its declared role, length and SHA-256.
The worker uses it to adopt a deterministic output left by an interrupted pack; it does
not overwrite, rename or delete a mismatched existing file.
