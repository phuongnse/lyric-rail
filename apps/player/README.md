# LyricRail desktop

This is the only supported desktop surface. The React UI contains the full-size
Player and hideable library/queue drawer. All filesystem, processing, package,
credential, recovery, catalog and Google Drive operations remain in the native Rust
backend.

The repository-styled top bar contains only Library, Activity and, outside macOS, a compact About utility.
Source, row and playback actions remain in their contextual homes instead of being
duplicated in a visible native menu. macOS alone keeps About in its minimal system
application menu for platform conventions.

Activity overlays the Player and has Tasks and Issues tabs. Tasks contains queued/running
work only; Issues owns failures and setup requirements across subsystems and can expand
generically linked task output inline. One native registry owns task state for karaoke processing, model installation, clip preparation,
local/Drive scans and Drive ciphertext caching. It exposes sequenced snapshots plus
batched realtime events; the frontend does not invent progress or ETA. Task output uses
a line/byte-bounded virtualized view with Pause view, Auto-scroll, stream filters and
Copy. Pending-batch shedding carries a gap signal and replays the retained ring before
later live lines. Pausing or closing Activity never pauses work, and completed tasks do
not form a visible history archive.

Native and frontend failures feed the typed Issues tab. Primary copy explains the
cause and next step; bounded redacted diagnostics are secondary. Missing pinned models
become a `setup-required` state with an explicit development-only Install models
resolver, license confirmation, bounded progress/cancellation, final provenance check
and automatic retry. Signed runtime contents are never mutated by this resolver.

Processing task IDs equal catalog item IDs, so an active row provides only its compact
summary and opens that exact Tasks record; failed/setup-required rows route to the
applicable Issue. Processing Cancel exists only in Activity.
Authenticated catalog task evidence is authoritative
for restored status/stage/progress/timestamps. Clear durable manifests and bounded log
tails are attached only after their fixed job ID and authoritative lyric hash match that
catalog evidence, without changing retry or cancellation semantics.
The native registry admits the complete bounded catalog queue but selects only a small
priority window without cloning it. Rows outside that window use the same by-ID native
lookup, so their View task/Cancel path remains available while frontend memory stays bounded.

Selecting exactly one supported local media file opens the Clip Editor. Native code
canonicalizes and identity-binds the regular file, runs bounded local-only ffprobe, and
derives a 16 kHz mono PCM audio preview into an anonymous delete-on-close handle. Its
opaque range responses are capped at 2 MiB; the frontend never receives a filesystem path.
PTS normalization and bounded silence padding keep its playhead on the original source
timeline even when an audio stream is delayed or shorter than the containing video.
The whole timeline is selected by default, while exact Start/End values flow into the
same sequential lyrics/processing queue. Preview, cancel and commit leave source bytes
unchanged. Independent positional range reads remain correct under concurrent WebView
requests. Packages and multi-file selections still enter the library directly.

The Player imports the canonical mark from `assets/brand/lyricrail-mark.svg`. Bundle
icons are generated from that same source with `npm run brand:icons`; do not substitute
framework artwork or hand-edit generated icon files.

The Player visual system is repository-owned: Be Vietnam Pro, custom CSS tokens and
the hand-authored SVG set in `src/Icon.tsx`. Icon-only controls must use `IconButton`,
which makes accessible labels and one portal-based hover/focus help system mandatory.
Placement is automatic: top-center by default, bottom at the top viewport edge, and
left/right aligned at the corresponding viewport edge. Visible-text actions do not
emit redundant tooltips. No external UI or icon theme defines the product's appearance.

```text
npm run dev:player
npm run build:player
```
