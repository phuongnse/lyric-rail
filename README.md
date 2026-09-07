# LyricRail

LyricRail is a private cross-platform karaoke system with two responsibilities:

1. The local core turns media plus authoritative UTF-8 lyrics into an authenticated,
   encrypted `.lrail` package.
2. One simple Player searches and plays packages from any number of local folders or
   user-selected Google Drive files and folders.

There is no separate Studio application. The Player owns one sequential local queue;
cloud sources are read-only and accept `.lrail` packages only.

## Player

The video uses the full window. Library, queue, source management and search live in
one drawer that overlays the Player and can be shown or hidden without resizing it.
Ready packages never autoplay merely because they were imported. Users choose a song,
play in order or shuffle, seek, change volume/fullscreen, and switch between
`Karaoke` and `Original` audio.

The Player's visual language is repository-owned rather than an off-the-shelf UI
theme: Be Vietnam Pro, the LyricRail gold/cyan/ink palette, custom CSS tokens and a
hand-authored SVG icon set. Icon-only actions include matching accessible labels and
hover/focus help text; compact UI copy is kept at a readable 10px minimum.

Visible actions have one home. Library groups Files/Folder under Local and Google Drive
under Cloud; lyric and retry controls stay on their song; playback controls stay in Player. Windows and
Linux use no duplicate native menu. The top bar adds only a styled Activity entry and a
small About utility on those platforms; macOS owns About in its minimal system menu.

Activity is the single detailed home for long work. Tasks contains only queued/running
work, while Issues contains failures and setup requirements from every subsystem. Both share
one native task ID/state model; active cards show the current stage, exact elapsed time,
determinate or indeterminate progress, measured ETA only when reliable, and Cancel only
where the underlying operation supports it. Realtime output is redacted before reaching
the WebView, delivered in bounded batches, replayable after reopening, and can be paused,
filtered or copied without pausing work. A burst that sheds one pending delivery batch
signals Activity to fill the gap from the retained ring. Completed work does not create a
separate visible history archive.

Errors enter the unified Issues tab with a stable code, plain-language cause,
secondary bounded diagnostics and allowlisted resolution actions. An Issue may carry a
generic related task ID and show that task's retained output inline without leaving Issues.
A processing failure links to its task and retry action. Missing processing
models put affected songs in `setup-required` without discarding their media, exact
lyrics or trim. A development checkout can install the pinned models after an explicit
size/license confirmation; progress and cancellation remain visible, every hash is
verified, and affected songs retry automatically. A signed runtime is immutable and
instead requires replacement by a complete verified pack.

Opening files or local folders produces one unified list:

- authenticated `.lrail` packages become ready;
- one selected local media file opens a Clip Editor with the whole timeline selected;
- local media with an exact-stem UTF-8 `.txt` sidecar enters the sequential queue;
- media without lyrics waits in place for Paste or TXT;
- processing progress, failures and completed packages update the same row.

The compact row is only a summary/link projection of the same processing task ID;
processing Cancel lives in Activity rather than a second row action. Status,
stage, progress and timestamps used after restart come from task evidence inside the
authenticated encrypted catalog. A fixed-path durable job manifest must still match
that catalog job ID and exact-lyric hash before its bounded log tail is attached;
unbound or malformed clear job evidence is ignored.
Large queues keep a bounded Activity snapshot/count, while every queued row can fetch
its stable task directly by ID for View task and Cancel; no active task is rejected or hidden
from its action path merely because it falls outside the snapshot window.

The Clip Editor supports millisecond Start/End entry, playhead capture, frame or 10 ms
nudging and a selection loop. Users can add the whole file or only the selected timeline.
Native ffprobe/ffmpeg are time/output bounded and restricted to local files plus a fixed
demuxer allowlist. For consistent WebView playback across all supported inputs, native
code derives a lightweight mono PCM preview into an anonymous delete-on-close handle and
serves it through an opaque range endpoint. Leading/trailing silence preserves the exact
source timeline even when its audio starts late or ends early. The selected source is
identity-bound while the editor is open; preview, cancel and commit never rewrite or
delete it. Selecting one `.lrail` package or multiple files keeps direct add behavior.

If the app stops after a package is published but before its stage status is saved,
Retry authenticates and binds that exact output to the job request, then continues
without repeating separation/media encoding or overwriting an existing file.

Search covers title, artist, composer and full lyric text. Vietnamese matching is
case- and diacritic-insensitive while displayed text stays unchanged. The catalog and
search source are authenticated and encrypted at rest.

## Thumbnails and lyric revisions

The core creates a compact encrypted WebP thumbnail from a representative frame and
overlays the exact first non-empty lyric line. Audio-only sources use a deterministic
local background. Older packages without artwork receive a neutral fallback.

The source lyric file is never edited. A package stores the exact user-confirmed UTF-8
text separately from derived timing. For a local typo correction with the same safe
sung structure, the revision acoustically re-aligns only changed lines against the
packaged Original track; whitespace-only edits do not load the model. It reuses the
encrypted representative thumbnail frame, generates fresh nonces for changed assets,
copies media ciphertext byte-for-byte, authenticates the complete replacement and only
then switches it into place. Unsafe structural changes fail closed and require local
reprocessing. Cloud packages must be copied local before editing.

## Google Drive playback

The desktop Picker grants narrow `drive.file` access to files or folders selected by
the user. Selected roots are stored in the encrypted catalog, so a folder rescan can
discover new packages and mark unavailable children offline without dropping rows.
Recursive discovery and provider page tokens are bounded before entries reach the queue.
LyricRail reads `.lrail` through bounded Drive byte ranges. Manifest,
playhead, seek and audio-switch reads preempt background download; spare bandwidth
completes a versioned ciphertext-only cache. Playback can therefore start before the
whole object downloads, while a complete cached package remains available offline.
Repeated opens of one object/version join the same in-flight transfer and stable task.

OAuth uses PKCE plus a loopback callback. Refresh tokens live only in the operating
system credential store. Configure a desktop OAuth client outside the repository:

```text
LYRICRAIL_GOOGLE_CLIENT_ID=...
LYRICRAIL_GOOGLE_CLIENT_SECRET=...   # only if the registered client requires it
```

Packages remain protected by the library master. On a new device, import an encrypted
recovery bundle and enter its passphrase once in the native recovery tool. The key is
validated against a package before it enters that device's credential store; routine
playback does not ask again.

## Package contents

```text
media/video.mp4
audio/karaoke.m4a
audio/original-reference.m4a|mp3
lyrics/authoritative.txt
lyrics/timing.json
lyrics/render-plan.json
metadata/release.json
presentation/template.json
artwork/thumbnail-base.webp
artwork/thumbnail.webp
```

Every private byte is encrypted with XChaCha20-Poly1305 in independently authenticated
chunks. The Player decrypts only bounded ranges in memory and writes no clear playback
file. `.lrail` protects a personal offline library; it is not commercial DRM and cannot
stop capture by a compromised or administrator-controlled operating system.

## Development

### Windows

Windows development is native PowerShell; WSL is neither required nor used. From a
fresh 64-bit Windows checkout with Microsoft App Installer (`winget`), run:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\bootstrap_windows.ps1
```

The host must permit unsigned local development builds. Windows Smart App Control in
`On`/enforcement mode blocks Rust build helpers and the unsigned Tauri development
binary; the bootstrap detects this before mutation and never disables or evades the
policy. Use Windows Security to make an informed host-level choice, or develop on a
separate Windows machine/VM whose application-control policy permits local builds.

The bootstrap installs the declared official Python 3.12, Node.js 24 LTS, rustup,
MSVC Build Tools and FFmpeg packages, creates `.venv`, rebuilds `node_modules` with
`npm ci`, installs a hash-locked patched pip plus pinned Rust/security tools, and
isolates Cargo output under `.dev/target-windows`. It then runs all repository
verification profiles and remains idempotent on later runs.
Inspect the read-only plan first with `-Plan`; add `-IncludeModels` to download and
verify the large pinned processing models after reviewing their licenses.
The development Player exposes the same pinned installer from a missing-model issue;
it is not available as a runtime-mutating action in signed builds.

An existing `.venv` is reused only when it is native Windows Python 3.12 x64 with the
expected repository prefix and `pyvenv.cfg`; incompatible environments are preserved
and rejected with cleanup guidance. `-Acceleration Auto` selects NVIDIA only when an
NVIDIA device is present, installs the pinned official CUDA build, and records NVIDIA
only after both PyTorch CUDA and ONNX `CUDAExecutionProvider` pass. CPU and GPU ONNX
distributions are mutually exclusive.

Launch the native Windows Player after setup:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\dev_player_windows.ps1
```

Google OAuth IDs, signing keys and other credentials are owner-supplied configuration
and are never generated by bootstrap. LyricRail's repository-owned vector mark and
bundle-icon regeneration contract live under `assets/brand/`.

### Other platforms

Portable source remains supported. Manual requirements are Node.js 24, Rust 1.98 with
a native toolchain, Python 3.12, FFmpeg/ffprobe and the pinned local models.

```text
npm ci
npm run dev:player
npm run build
python scripts/lyricrail.py run "Song - Artist.mp4" --lyrics "Song - Artist.txt"
```

Canonical verification is owned by `.process/project.json`:

```text
npm run build
npm test
python -m pytest -q
cargo test --workspace --locked
cargo clippy --workspace --all-targets --locked -- -D warnings
```

Package, key, recovery, remote-range and cache changes also require the `security`
profile. The readiness stage remains `building`; open signing, clean-host, runtime
delivery, updater, incident-response and independent-review gates prevent a stable
production-security claim. See [security acceptance](docs/SECURITY_ACCEPTANCE.md) and
[release status](docs/RELEASE_STATUS.md).
