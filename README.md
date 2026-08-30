# LyricRail

LyricRail is a private, cross-platform karaoke system with two desktop tools:

- **LyricRail Studio** turns local media plus authoritative UTF-8 lyrics into a
  compact, authenticated `.lrail` package.
- **LyricRail Player** opens only authenticated packages, renders word timing
  dynamically, and switches between `Karaoke` and `Original Reference` audio.

The current version is **0.8 beta / private release candidate**. The Windows
apps and a separately verified local runtime pack are implemented and exercised.
The installers are intentionally unsigned and do not embed the 17.1 GB runtime.
macOS and Linux are source targets covered by CI, but signed/notarized release
artifacts still require platform credentials and clean-host release testing.

Cross-platform here means one package format, crypto core, pipeline contract,
and UI contract with small native adapters where the operating systems differ.
Encrypted-workspace evidence uses BitLocker plus a least-privilege broker on
Windows, APFS/FileVault status on macOS, and the mounted block-device
dm-crypt/LUKS chain on Linux. Windows-only service build and installer hooks
live exclusively in `tauri.windows.conf.json`; they are not evaluated by
macOS/Linux builds. See [`docs/PLATFORM_ARCHITECTURE.md`](docs/PLATFORM_ARCHITECTURE.md).

## Package output

Studio emits one `.lrail` file containing:

```text
media/video.mp4                    picture without burned lyrics
audio/karaoke.m4a                  default instrumental track
audio/original-reference.m4a|mp3   reference vocal track, source codec preserved
lyrics/timing.json                 authoritative syllable timing and roles
lyrics/render-plan.json            two-line dynamic presentation schedule
metadata/release.json              title, artist, description, credits, rights
presentation/template.json         role colors and layout policy
sources/visual-license.json        optional visual provenance
```

Media is not compressed again by the package layer. H.264 source video is
stream-copied when no timeline edit is needed; otherwise video is encoded once
at H.264 High/CRF 18/slow. Karaoke, which is a newly processed mix, uses AAC-LC
256 kbps. A complete AAC or MP3 Original Reference timeline is remuxed without
decoding or re-encoding; trimmed or non-portable codecs use the AAC fallback.
Integration fixtures also compare encoded-packet hashes across the supported
MP4/MKV remux paths. The encrypted release metadata records the actual codec,
size, duration, and whether a transcode occurred.

Every asset is split into authenticated 1 MiB chunks and encrypted with
XChaCha20-Poly1305. The Player decrypts bounded ranges in memory and does not
write a clear playback file. `.lrail` is protection for a personal offline
library, not commercial DRM; an administrator, debugger, screen recorder, or
compromised OS can capture media while it is being decoded.

## Desktop development

Requirements: Node.js 24, Rust stable with a native C/C++ toolchain, Python
3.12, FFmpeg/ffprobe, and the pinned ML models.

```text
npm ci
npm run dev:studio
npm run dev:player
```

Production frontend builds:

```text
npm run build
```

Windows production bundles:

```text
npm run tauri build --workspace @lyricrail/studio
npm run tauri build --workspace @lyricrail/player
```

Native tests and lint:

```text
cargo test --workspace --locked
cargo clippy --workspace --all-targets --locked -- -D warnings
```

## Pipeline CLI

The native Studio starts the pinned Python pipeline without a shell. The CLI is
also available directly:

```text
python scripts/lyricrail.py config validate
python scripts/lyricrail.py doctor --production
python scripts/lyricrail.py run "Song - Artist.mp4" --lyrics lyrics.txt --no-upload
```

Lyrics are immutable input: models may align the supplied words and classify
roles, but may not infer, replace, or correct them. Each run verifies exact model
revisions/checkpoint SHA-256 values before processing. Clear work files exist
during production; after the final package passes full authentication, optional
cleanup removes only that job's intermediates. It does not claim SSD secure
erasure and never deletes the source media.

## Native key recovery

Passphrases are read only by the native executable, never by Studio JavaScript:

```text
lrail recovery-export --output library.lrail-recovery
lrail recovery-inspect library.lrail-recovery
lrail recovery-verify library.lrail-recovery
lrail recovery-restore library.lrail-recovery --library D:\Karaoke
```

Restore is fail-closed: it requires at least one package, authenticates the
complete selected library, rejects active rotation, and never overwrites a
different current key. The format is documented in
[`docs/LRAIL_RECOVERY_V1.md`](docs/LRAIL_RECOVERY_V1.md).

## Verification

LyricRail pins `engineering-process` v1.0.1 with a complete hash lock. Non-trivial
changes use the managed lifecycle in `AGENTS.md`: define the contract, plan the
work, implement, verify the immutable checkpoint, obtain independent review, and
resolve every required finding before completion. Install and inspect the authority
with:

```text
python -m pip install --require-hashes -r requirements/process.txt
processctl adoption check --project-root . --requirements-lock requirements/process.txt
processctl doctor --project-root . --profile python
```

The required profiles are `python`, `frontend`, and `rust`; security-sensitive
changes also require `security`. CI invokes the same profile definitions on Linux,
macOS, and Windows where applicable.

The versioned `desktop-media@1` readiness path currently reports `building`, not
production. Existing source-quality evidence is enforced while the stable signing,
real-host, dependency, recovery, runtime delivery, updater, incident, and independent
security-review gaps remain planned in the consumer-owned readiness sidecar and
normative acceptance documents.

```text
python -m pytest -q
cargo test --workspace --locked
npm run build
rustup toolchain install nightly-2026-07-01 --profile minimal
cargo +nightly-2026-07-01 fuzz run package_open -- -runs=1000 -max_len=512
npm audit --audit-level=moderate
cargo audit
python -m pip_audit --local --progress-spinner off
```

Run the fuzz command on Linux or WSL2; upstream cargo-fuzz does not provide a
supported native Windows ASan path. The toolchain is pinned because
libFuzzer/AddressSanitizer must use a matching Rust/LLVM ABI. Current dependency
findings and platform-specific release blockers are documented in
[`docs/SECURITY_EXCEPTIONS.md`](docs/SECURITY_EXCEPTIONS.md).

## Security and format documentation

- [`docs/THREAT_MODEL.md`](docs/THREAT_MODEL.md)
- [`docs/LRAIL_FORMAT_V1.md`](docs/LRAIL_FORMAT_V1.md)
- [`docs/LRAIL_RECOVERY_V1.md`](docs/LRAIL_RECOVERY_V1.md)
- [`docs/KEY_MANAGEMENT.md`](docs/KEY_MANAGEMENT.md)
- [`docs/RUNTIME_PACK.md`](docs/RUNTIME_PACK.md)
- [`docs/PLATFORM_ARCHITECTURE.md`](docs/PLATFORM_ARCHITECTURE.md)
- [`docs/SECURITY_ACCEPTANCE.md`](docs/SECURITY_ACCEPTANCE.md)
- [`docs/RELEASE_STATUS.md`](docs/RELEASE_STATUS.md)
- [`SECURITY.md`](SECURITY.md)

No license has been selected for the LyricRail source code. Bundled Be Vietnam
Pro fonts retain their SIL Open Font License in `assets/fonts`.
