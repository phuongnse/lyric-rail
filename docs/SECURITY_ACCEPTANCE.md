# LyricRail security acceptance

A release must not be described as production-grade security until every gate in
this document has current evidence for every supported platform.

## Format and cryptography

- [x] `.lrail` v1 parser implements every bound in `LRAIL_FORMAT_V1.md`.
- [x] XChaCha20-Poly1305 uses a maintained implementation without custom crypto.
- [x] A fresh CSPRNG DEK is generated for every package.
- [x] Automated tests prove nonce uniqueness for generated fixtures.
- [x] Associated-data tests reject package, asset, index, offset, and length swaps.
- [x] Missing, duplicated, reordered, truncated, appended, and corrupted data fail.
- [x] No plaintext is released before the corresponding authentication succeeds.
- [x] Known-answer and cross-implementation fixtures are versioned and exercised
      by both RustCrypto and libsodium/PyNaCl.

## Key management

- [x] Release builds contain no hard-coded or test key path.
- [ ] OS credential storage succeeds or fails closed on each platform.
- [x] Keys never cross into frontend JavaScript, logs, command lines, or telemetry.
- [x] Recovery export, verification, restore, corruption, key-loss,
      conflicting-key, empty-library, active-rotation, and wrong-passphrase paths pass.
- [x] Rotation is transactional, cross-process writer-locked, and has tests for
      every durable replacement/switch/cleanup boundary plus missing-key paths.
- [x] Application-owned keys, recovery passphrases, and runtime signing-key text
      are memory-locked, zeroized before unlock, and fail closed if locking fails.

## Package parser and player

- [x] Parser arithmetic uses checked operations and rejects all out-of-bounds reads.
- [x] Asset/chunk/string/node limits are covered at the exact boundary; v1 has
      identity encoding only and rejects unsupported compression identifiers.
- [x] Coverage-guided fuzzing reaches header, envelope, manifest, and virtual reads.
- [x] AddressSanitizer fuzz smoke completes 1,000 runs on a supported Linux host
      with a pinned Rust nightly and cargo-fuzz version.
- [x] A malicious package cannot select an arbitrary filesystem path or URL.
- [x] Playback supports random seek and audio switching without a clear temp file.
- [x] Ranged responses are capped at 2 MiB; Player keeps no decrypted on-disk or
      application-level media cache.
- [x] Player keeps an authenticated open handle if the path moves and fails
      closed without panic when live package ciphertext is corrupted.

## Studio workspace

- [x] The UI states that processing intermediates are cleartext.
- [x] Windows release Studio surfaces native BitLocker protection state and
      fails closed unless both protection and conversion status are complete.
- [x] The Windows least-privilege broker uses a closed binary protocol, rejects
      remote clients, and binds the named-pipe server PID to the exact running
      SCM own-process service; MSI/NSIS service integration builds successfully.
- [ ] The broker and installers are signed, then clean-install/upgrade/uninstall
      tested while a standard desktop account verifies BitLocker end to end.
- [x] macOS APFS/FileVault and Linux mountinfo/sysfs dm-crypt/LUKS adapters exist,
      return the shared fail-closed state contract, and compile/test in their
      platform targets.
- [ ] macOS and Linux encrypted-workspace adapters pass clean-host runtime tests
      on supported real machines and filesystems.
- [x] Cleanup is scoped to the current job and never claims SSD secure erasure.
- [x] A package is fully verified before optional intermediate cleanup.
- [ ] Recovery is verified before the last clear master may be removed.

## Application and supply chain

- [x] Tauri capabilities and CSP grant only documented minimum permissions.
- [x] Release Studio requires an exhaustive Ed25519-signed runtime manifest,
      pins Python/FFmpeg/ffprobe/lrail paths, and rejects file additions,
      removals, symlinks, version/platform mismatch, or content changes.
- [x] Mutable jobs, cache, logs, input, and credentials are outside the signed
      runtime root.
- [x] Runtime executable paths and every sidecar byte are signed/hashed; release
      process arguments and environment construction are native and fixed.
- [ ] Windows binaries/installers are Authenticode signed.
- [ ] macOS binaries are hardened, signed, and notarized.
- [ ] Linux packages have documented signature verification.
- [ ] Update artifacts require a pinned signature and reject rollback by default.
- [x] Dependencies are locked; five CycloneDX SBOMs and vulnerability scans are
      current release artifacts.
- [ ] Linux releases remove or explicitly resolve the Tauri GTK3/glib 0.18.5
      unsoundness and unmaintained dependency chain.
- [ ] CI builds and tests all supported architectures from clean environments.

## Assurance

- [ ] Threat model review has no unresolved high-severity finding.
- [ ] An independent reviewer has assessed format, crypto use, key lifecycle, parser,
      player boundary, and update chain.
- [ ] All critical/high findings are fixed and retested.
- [x] Security limitations are present in product documentation and both UIs.
- [ ] Incident response, key compromise, and signing-key recovery are rehearsed.
