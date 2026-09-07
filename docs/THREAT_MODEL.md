# LyricRail threat model

Status: normative for `.lrail` v1, the local processing core and Player.

## Objective and limit

LyricRail protects a personal karaoke library at rest and authorizes a new device only
through the recovered library master. The Player releases only authenticated bounded
bytes needed for playback and never creates a clear media cache.

This is not commercial DRM. An administrator, debugger, compromised OS, screen
recorder, virtual audio device or external capture device can recover decoded media.
The product must not claim otherwise.

## Protected data

- karaoke/reference audio, video and first-line thumbnails;
- authoritative lyrics, timing, roles, title, artist and composer;
- local/Drive source identifiers and search catalog;
- package DEKs, library/catalog keys, recovery passphrases and OAuth refresh tokens.

## Trust boundaries

1. **Local processing worker.** Local inputs and per-job intermediates are cleartext.
   One low-priority worker processes jobs sequentially; every job owns its directory,
   cancellation markers, OS-backed run lease and artifacts. An interrupted `running`
   manifest can be claimed only after the prior process releases its kernel lease and
   the source/lyric fingerprints match. Optional cleanup follows complete package
   authentication and never removes source or another job. SSD secure erasure is not
   claimed.
2. **Package/revision core.** All paths and offsets are untrusted. Packaging creates
   fresh keys/nonces. Exact confirmed text is distinct from its timing/render derivations.
   A lyric revision may replace only bounded declared assets after affected-scope alignment,
   preserves unchanged media ciphertext and switches files only after full verification.
   Player and CLI device-vault revision publication select the current master under
   the same cross-process lock as rotation and hold it through publication/rollback.
   Revision maps authenticated `referenceGroup` word scopes to existing display rows;
   semantic and display line counts remain distinct.
   A published output left by interruption is adopted only if request-bound verification
   matches authenticated manifest fields, every current input hash and all package chunks;
   an ambiguous existing output is never overwritten or deleted automatically.
3. **Local or remote storage.** Package bytes, Drive metadata, HTTP status, range
   headers, redirects, bodies, pagination tokens, cache blocks and object versions are
   attacker-controlled. Response bodies are capped before allocation and the
   same parser limits and AEAD checks apply to both source types.
4. **Cloud cache and catalog.** Media cache files contain version-keyed `.lrail`
   ciphertext only and enforce hard byte/file ceilings with bounded-memory deterministic
   eviction. Active partials carry a per-process session prefix and are excluded from
   deletion until that session ends; concurrent foreground writers are capped at eight
   plus one background writer. Selected Drive roots and per-location availability are authenticated catalog
   state. Catalog/source/search records are authenticated and
   encrypted under a separate OS-stored catalog key. No plaintext lyric index is
   persisted.
   Drive listing requests the provider's `incompleteSearch` flag and refuses partial
   reconciliation. Offline completion requires every retained ciphertext block;
   objects exceeding cache capacity remain online-only. Folder enumeration applies
   its remaining global entry budget before collection and sorting.
5. **Credential stores.** The library master, catalog key and Drive refresh token use
   the platform credential service. Content keys and recovery passphrases stay in
   locked/zeroized native memory and never enter frontend JavaScript, command lines or
   logs. An unavailable store fails closed.
6. **OAuth boundary.** Google authorization uses the system browser, desktop Picker,
   per-file `drive.file` scope, PKCE, a random state and loopback listener. Production
   client configuration remains external. Revocation or 401 responses stop access;
   recursive discovery and provider page tokens are globally bounded before enqueue.
   Cached complete ciphertext may remain playable if the package key is authorized.
7. **Player/media decoder.** The native core maps media requests to fixed package
   assets, caps each response at 2 MiB and authenticates every chunk before returning
   plaintext. Playback/seek/audio-switch I/O preempts processing, indexing and cache
   fill. The WebView receives media bytes but never keys. Native and frontend failures
   enter a bounded Activity/issue registry; resolution values are a closed enum rather than
   command names. The development model resolver accepts only an exact issue ID,
   explicit license confirmation, the canonical installer under the runtime root and
   the resolved Python executable. Audio assets use manifest-owned HTTPS URLs, byte
   lengths and SHA-256 values; bounded downloads reach only unique sibling temporary
   files and replace a cache destination atomically after verification, so interrupted
   or hostile same-name bytes fail closed without destroying the prior entry. It bounds
   subprocess output, supports cancellation and requires final pinned provenance before
   retry; signed roots reject mutation.
   Diagnostics redact runtime/user paths, remote query-bearing addresses and
   credential-shaped values before storage, events, display or clipboard copy.
   The required presentation template is authenticated before parsing; asset kind/media
   type, supported render modes, finite geometry bounds and strict hex colors are checked
   natively before normalized values reach CSS. Cue timing and slot decisions come only
   from the authenticated bounded render plan.
8. **Local clip preview.** A selected path and its media bytes are untrusted. Native code
   rejects links and unsupported/non-regular files, canonicalizes the path, bounds file
   size, and guards platform file identity/change metadata from probe through commit.
   ffprobe/ffmpeg use local-file and fixed-demuxer allowlists plus time/output limits.
   Initial preview reads metadata and serves the pinned source, rechecking identity at
   each opaque positional range request (maximum 2 MiB). It does not transcode or decode
   the full file. Nearby frame inspection uses decoded source PTS normalized by format
   start time, with a nine-second requested interval, 1 MiB/16,384-frame/20-second bounds;
   seeking may start at an earlier keyframe. One active query and one replaceable UI
   destination bound rapid seeking; request IDs scope cancellation and stale rejection.
   The UI retains at most two overlapping frame windows and fetches missing neighbors;
   requested probe bounds alone are not evidence of adjacent frames. Empty or stalled
   inspection is explicit and retryable. Nonzero container origins require explicit
   compatibility preparation before playback; browser time is not assumed normalized.
   Preparation is cancellable from its modal and Activity, including scheduler wait,
   subprocess work and the final publication boundary. Owned children are killed/reaped.
   Video sources are bounded to 8K pixel count. Unsupported webview codecs expose an
   explicit whole-file compatibility fallback, never an automatic long conversion.
   Replacement authenticates the existing clip ID and pinned source identity, retaining
   the previous session and editor drafts until successful publication. A failed attempt
   leaves the old preview usable; closing it prevents late replacement publication.
   That fallback uses anonymous delete-on-close 16 kHz mono PCM with normalized PTS and
   leading/trailing silence, plus an anonymous muted H264 proxy capped at 2 GiB/300 seconds,
   with passthrough presentation timestamps and a bounded one-million-frame/24 MiB/
   120-second frame inspection. Audio remains the source-timeline clock. Frame trim
   boundaries are floored once to native integer milliseconds (less than 1 ms early);
   frame preview seeks retain measured presentation timestamps. Explicit time entry
   accepts source milliseconds, including audio before video begins, without resnapping.
   Invalid drafts block batch admission. No raw source
   path is served. Batches validate every range/title and source identity before one
   encrypted catalog publication; a save failure retains the prior catalog and preview.
   Successful admission consumes the locked session so retries cannot duplicate songs.
   Typed optional section identity prevents path-based rescan deduplication from merging
   independent songs, including after processing completion and package refresh.
   Each section waits for its own exact lyrics. Cancel and commit
   close session state but never mutate the source.
   Catalog v4 reads/migrates v1-v3 with absent section identities. Its version prevents
   older Players from opening and silently merging independently admitted sections;
   package v1 and recovery bundle formats are unchanged.
9. **Recovery.** A native executable owns passphrase input. Restore rejects active
   rotation, wrong/corrupt bundles and conflicting current keys, and verifies at least
   one complete package before storing a previously missing master. A cloud-only new
   device downloads one ciphertext package for that verification.
10. **Runtime, installers and updates.** They are untrusted until signed and verified
   against pinned release policy. The current source/runtime work does not close those
   stable-release gates.
11. **Task progress/output.** Tool output and progress are untrusted and may be silent,
   hostile, oversized, rapid or non-monotonic. The native registry caps task history,
   per-task lines/bytes and pending event bytes; assigns one sequence under a lock; emits
   at most every 100 ms; signals and replays retained-ring gaps after burst shedding; and
   computes ETA only from a sufficient monotonic measured window. Python drains
   stdout/stderr concurrently with bounded lines/channels and
   rotates projected durable logs. Cancellation continues during post-exit pipe drain;
   Unix process groups and guarded Windows Jobs own command descendants. A Windows
   helper runs by absolute owned script path in isolated Python mode, ignoring cwd
   and `PYTHONPATH` for helper imports, and waits for Job assignment before launching
   the target. Post-exit draining
   has a five-second limit. The WebView virtualizes output and pausing its view
   never changes worker execution. Restarted status, stage, percentage and timestamps
   come from encrypted/authenticated catalog task evidence. Fixed-path bounded clear job
   evidence contributes only diagnostics after its job ID and exact lyric digest match
   that catalog; authenticated package state is required after verified cleanup.
   Local OS paths remain exact internally, including Windows extended-path forms.
   Internal Python JSON escapes lone surrogate code units for lossless path round-trip,
   while `src/lyricrail/diagnostic_contract.json` defines the closed shared diagnostic
   vocabulary and typed finite progress fields for Python, Rust and TypeScript.
   Arbitrary text and unknown fields are withheld before durable logging, native
   ring/event storage, command-error/status IPC and clipboard output. Unsupported
   diagnostic objects use a fixed sentinel without string conversion; only actual
   string keys enter the closed vocabulary. Bounded malformed JSON and oversized
   numeric fields cannot turn diagnostic projection into a processing failure. Numeric progress and
   fixed stage/status messages remain available; raw subprocess tails and argument
   values are not retained as safe diagnostics. Diagnostic traversal has global node/byte/depth limits,
   rejects non-finite/out-of-range numbers and preserves valid surrogate pairs. Worker
   control IDs/package paths are validated and never blanket-rewritten; portable display
   labels are made valid before the package filename exists. Authoritative lyric bytes
   are excluded from that lossy diagnostic boundary. The native launcher enables
   CPython UTF-8 mode even when Windows creates the worker without a console; development
   stdin/stdout are strict UTF-8 and valid stderr Unicode is UTF-8 encoded. Worker readers
   are generation-bound, so stale events, stderr or EOF cannot mutate a replacement
   worker. Structured events validate their generation under the same scheduler lock as
   their worker/request transition. Unexpected current-generation stdout closure takes
   the active request once through the existing failed-task/Issue path and leaves queued
   requests for normal dispatch.

## Threats in scope

- theft/copying of packages, cache or catalog files;
- unauthorized device use and wrong/conflicting recovery;
- malformed, truncated, reordered, duplicated or swapped chunks/manifests;
- hostile HTTP ranges, short bodies, stale Drive versions and cache corruption;
- OAuth state/code interception, token disclosure and authorization revocation;
- path traversal, symlinks, unbounded folder scans, integer overflow and allocation;
- plaintext leakage through persistent indexes, logs, temporary playback files or UI;
- nonce reuse or partial publication during lyric revision;
- playback starvation by processing or background network work;
- downgrade to weaker parser, package, recovery or updater behavior.

## Outside the claim

- administrator/root or kernel compromise while content is decoded;
- recording audio/video or analog output;
- source media and lyric files intentionally retained by the user;
- cloud/service/network availability;
- legal rights for source music/video;
- recovery after both active and recovery key material are lost.

## Stable-release controls

XChaCha20-Poly1305, unique nonces, domain-separated AAD, manifest graph commitment,
verify-before-release, strict parser/network/cache/catalog bounds, credential storage,
locked/zeroized native secrets, tested recovery/rotation/revision, signed bundles and
updates, dependency audits, fuzzing and independent review are mandatory. Open gates in
`SECURITY_ACCEPTANCE.md` prevent a production-security claim.
