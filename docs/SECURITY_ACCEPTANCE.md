# LyricRail security acceptance

A release must not be described as production-grade security until every applicable gate has
current evidence for each claimed platform.

## Package, parser and revision

- [x] `.lrail` v1 bounds, XChaCha20-Poly1305, fresh per-package DEKs and unique nonces.
- [x] Header/envelope/manifest/chunk swaps, corruption, truncation and graph errors fail.
- [x] No chunk plaintext is released before its authentication succeeds.
- [x] Known-answer RustCrypto/libsodium fixtures and bounded parser fuzzing are versioned.
- [x] Local and remote bytes use the same authenticated random-access reader.
- [x] Remote short/malformed ranges and corrupted cache bytes fail closed.
- [x] Lyric revision uses fresh changed-asset identities/nonces, preserves unchanged
      media ciphertext, verifies before atomic switch and retains rollback until success.
- [x] Exact confirmed lyric text is stored as its own authenticated asset; changed words
      pass an affected-scope acoustic alignment gate before revision publication.
- [x] Legacy v1 packages without a thumbnail retain a neutral Player fallback.

## Keys and recovery

- [x] Release packages contain no hard-coded/test key path.
- [ ] OS credential storage succeeds or fails closed on every claimed platform.
- [x] Keys and recovery passphrases never enter frontend JavaScript, logs or arguments.
- [x] Recovery export/verify/restore, wrong/corrupt/conflicting/empty-library and active
      rotation paths are tested.
- [x] A cloud-only restore validates a complete downloaded ciphertext package before
      installing a missing master.
- [x] Windows, macOS and Linux launch the passphrase-owning native tool without routing
      the secret through frontend JavaScript; real-host drills remain release gates.
- [x] Native secrets are memory-locked and zeroized; lock refusal fails closed.
- [ ] Recovery is verified before the last clear master may be removed in real-host drills.

## Local processing and lyric integrity

- [x] Only regular local media enters processing; the CLI/worker reject cloud objects and
      remote locators. Clip selection supplies only a canonical native path plus bounded
      Start/End values.
- [x] The Clip Editor rejects links, unsupported/non-regular files, empty files and files
      over 8 GiB; local ffprobe/ffmpeg have fixed protocol/format allowlists plus output,
      duration and time bounds. Every format receives a bounded mono PCM preview in an
      anonymous delete-on-close handle rather than relying on WebView source codecs;
      PTS normalization and silence padding preserve the complete source timeline.
- [x] Preview never rewrites or deletes selected source media; cancel and commit neither
      copy, rewrite nor delete it. Only one local clip session is live; it guards and
      rechecks platform file identity, size and change times before commit while its source
      path remains behind native code.
- [x] Exact-stem UTF-8 sidecars pair deterministically; ambiguous/missing lyrics wait.
- [x] Supplied source lyric files are never modified or silently corrected.
- [x] Explicit safe corrections re-align only affected lines; whitespace-only changes
      preserve timing and unsafe structural changes require local reprocessing.
- [x] One isolated low-priority worker processes jobs sequentially with bounded events,
      durable retry/cancellation and no shell command construction. An OS-backed run
      lease safely recovers interrupted `running` manifests without racing a live worker.
- [x] Package-stage retry adopts an interrupted output only after request-bound native
      verification of manifest fields, all input hashes and every encrypted chunk;
      mismatched outputs are preserved and identical artifacts merge idempotently.
- [x] Playback I/O takes priority over worker, index and background-cache work.
- [x] One bounded native task registry owns processing/model/clip/scan/Drive-transfer
      state. Realtime tool pipes have line/channel/ring limits, ten-Hz native batching,
      central redaction, measured-only ETA and controlled cancellation; restored task
      state comes only from encrypted/authenticated catalog evidence, while clear job
      manifests/log tails require the matching catalog job ID and exact authoritative-
      lyric hash before attachment.
- [x] Pending live-output shedding signals replay from the retained ring; JSON/underscore
      credential names, Bearer values and spaced absolute paths are redacted before
      durable logs, native events or clipboard copy.
- [x] OS-native paths round-trip internal valid UTF-8 JSON through ASCII surrogate
      escapes; lone surrogates are replaced only in bounded diagnostics and portable
      display labels. Worker diagnostics use one global depth/node/byte budget and
      standards-compliant finite JSON; control IDs/package paths remain exact or fail.
      Strict UTF-8 authoritative lyric bytes/text/hashes never enter that sanitizer.
      Every native launch enables CPython UTF-8 mode before worker module execution;
      generation-bound readers ignore stale events/EOF, event validation and transition
      share one scheduler lock, and unexpected current-worker EOF terminalizes the active
      task once while preserving queue order.
- [x] Optional cleanup is scoped to one fully verified successful job and does not claim
      SSD secure erasure.

## Player, catalog and cloud

- [x] Playback supports seek and audio switching without a clear playback file.
- [x] Native package and opaque local-clip media responses are capped at 2 MiB;
      package-selected paths and preview filesystem paths are denied. Clip ranges use
      positional reads, so concurrent requests cannot share a mutable file cursor.
- [x] Multiple local sources use bounded, asynchronous, symlink-safe scans.
- [x] Catalog paths, metadata, lyrics and search records are authenticated/encrypted at rest.
- [x] Catalog v3 migrates v1/v2 media entries with Disk/no-trim defaults, retains local
      clip trim metadata and setup-required state, and rejects future schemas.
- [x] Drive cache contains versioned package ciphertext only and has deterministic limits.
- [x] Repeated opens of one Drive object/version share one stable task and in-flight
      transfer; scan stages start before provider work and finish at attempted-root totals.
- [x] Cache eviction cannot remove a live writer's session-owned partial; foreground
      range concurrency and transient partial overhead are explicitly bounded.
- [x] Desktop OAuth uses Picker per-file access, PKCE, random state and loopback callback;
      refresh tokens stay in the OS credential store.
- [x] Controlled loopback fixtures exercise OAuth exchange/revocation, folder pagination,
      401 refresh, pre-allocation range bounds, version invalidation, progressive package
      open, alternate-track reads and complete-cache offline playback.
- [x] Recursive Drive discovery and retained page tokens are bounded before queue insertion.
- [ ] Owner-supplied Google client registration passes live consent, revocation, folder,
      shared-drive, throttling and offline tests without requesting broader scope.

## Application and supply chain

- [x] Tauri capabilities/CSP grant only documented minimum frontend permissions.
- [x] Native/frontend failures enter one bounded typed Activity Issues tab; repeated keys
      deduplicate, user copy is separate from redacted technical detail, and resolution
      kinds cannot name arbitrary commands, paths or network destinations. Processing
      failures link to the same task/catalog ID and closed retry action.
- [x] Missing models preserve source/lyrics/trim in setup-required state. Unsigned
      development roots may invoke only the canonical pinned installer after explicit
      license confirmation. Manifest-owned HTTPS URLs, exact byte lengths, streamed
      SHA-256 and atomic verified publication repair interrupted caches without trusting
      their partial bytes; output/cancellation remain bounded, signed runtimes refuse
      mutation and successful final verification alone permits retry.
- [x] Windows/Linux render no duplicate native menu; contextual actions have one visible
      home, shared in-window shortcuts ignore editable controls, and macOS retains only
      its minimal application convention menu.
- [x] The WebView receives only the main-window opaque local clip-preview scheme; the CSP
      grants no remote media or network connection source.
- [x] Dynamic karaoke presentation comes from an authenticated required package asset;
      native code validates its kind/media type, supported modes, bounded finite geometry
      and strict colors before the WebView consumes render-plan-owned slots and cues.
- [x] Clip dialog teardown clears its opaque session while leaving the selected file
      unchanged and closing its anonymous preview handle.
- [x] Release local processing requires an exhaustive signed runtime manifest and pinned
      Python/FFmpeg/ffprobe/lrail paths.
- [x] Dependencies are locked and repository-owned audits are required.
- [ ] Windows binaries/installers are Authenticode signed and clean-host tested.
- [ ] macOS binaries are hardened, signed, notarized and real-host tested.
- [ ] Linux packages have signature verification and resolve the GTK/glib advisory chain.
- [ ] Signed updater/rollback policy and protected release metadata are implemented.
- [ ] CI builds and tests every claimed architecture from clean environments.

## Assurance

- [ ] Threat-model review has no unresolved high-severity finding.
- [ ] An independent reviewer has assessed format, keys, recovery, revision, parser,
      remote transport/cache, OAuth, Player boundary and update chain.
- [ ] All critical/high findings are fixed and retested.
- [x] Security limits are present in product and repository documentation.
- [ ] Incident response, credential revocation and signing-key recovery are rehearsed.
