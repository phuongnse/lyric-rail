# LyricRail threat model

Status: normative for `.lrail` format version 1 and the LyricRail Player.

## Security objective

LyricRail protects a personal karaoke library at rest and makes copied packages
unusable on devices that have not been authorized. A valid Player decrypts only
the authenticated bytes needed for playback and does not materialize a clear
media file on disk.

The security claim is deliberately narrower than commercial DRM. Once media is
decoded for a human, an administrator, debugger, compromised operating system,
screen recorder, virtual audio device, or external capture device can recover
the presentation. LyricRail must never claim to prevent those attacks.

## Protected assets

- karaoke and reference audio;
- background video and artwork;
- authoritative lyrics, acoustic timing, and singer-role assignments;
- song metadata, source URLs, and private production notes;
- package data-encryption keys and library key-encryption keys.

## Trust boundaries

1. **Studio processing workspace.** Inputs and model intermediates are cleartext.
   The workspace must reside on an OS-encrypted volume. Secure deletion on SSDs
   is not promised. Release Studio accepts only the shared `protected` result.
   Windows queries BitLocker directly or uses an SCM/PID-authenticated,
   least-privilege read-only broker without elevating Studio. macOS requires a
   canonical FileVault-positive APFS result. Linux requires an active
   dm-crypt/LUKS mapping in the mounted block-device chain. Missing, migrating,
   remote, overlay, unsupported, malformed, or timed-out evidence fails closed.
2. **Packager core.** Reads verified artifacts and emits one authenticated,
   encrypted package. It handles plaintext only for the duration of packaging.
3. **Package storage.** Considered attacker-controlled. Every byte read from a
   package is untrusted until its bounds and authentication are verified.
4. **OS credential store.** Holds or protects library key-encryption keys. An
   unavailable credential store is an error; there is no silent plaintext-key
   fallback. Application-owned key and native passphrase buffers are locked out
   of paging and zeroized before unlock; refusal to lock memory is also fatal.
5. **Player core.** Authenticates and decrypts only bounded requested chunks,
   then feeds those bytes to the WebView media decoder through a private scheme.
   It keeps no decrypted on-disk cache. Frontend JavaScript never receives
   content keys; CSP also prevents script fetches to the media scheme.
6. **Updater and installers.** Considered untrusted unless their signatures are
   verified against a pinned release key.
7. **Studio runtime pack.** Release Studio trusts a runtime only after strict
   Ed25519 verification with its embedded public key and exhaustive SHA-256
   inventory. Runtime contents are immutable; mutable user data lives under the
   platform application-data directory. The signing private key is never part
   of an application bundle or runtime pack. The current private Windows RC2
   pack is verified but distributed separately from the Studio shell; its RC
   signing key and model-license status are not stable-release custody.

## Threats in scope

- opening a package in a generic player or archive utility;
- copying a package to an unauthorized device;
- offline theft of a package or backup disk;
- modification, truncation, insertion, deletion, duplication, or reordering of
  encrypted chunks;
- swapping chunks or manifests between packages;
- malformed packages intended to trigger path traversal, integer overflow,
  excessive allocation, parser confusion, or decoder abuse;
- accidental disclosure through logs, temporary cleartext media, crash reports,
  or updater artifacts;
- loss, rotation, recovery, and revocation of key-encryption keys;
- downgrade to a format or application version with weaker validation.

## Threats outside the security claim

- a local administrator/root user while content is playing;
- kernel compromise, injected code, debuggers, process dumps, and DMA attacks;
- recording decoded audio/video, including analog capture;
- disclosure of the original inputs retained by the user;
- legal or copyright authorization for packaged source material;
- availability after both the active key and recovery material are lost.

## Stable-release required controls

- standard, authenticated encryption only; no proprietary cipher;
- a random 256-bit data-encryption key per package;
- independent key-encryption keys and data-encryption keys;
- unique nonces and domain-separated associated data for every encrypted object;
- an authenticated manifest that commits to the complete asset/chunk graph;
- verify-before-release of every plaintext chunk;
- strict limits before allocation or media decoding;
- no hard-coded, source-controlled, environment-variable, or logged content key;
- fail-closed OS memory locking and zeroization for application-owned secrets;
- OS credential storage, explicit encrypted recovery, and tested key rotation;
- signed application bundles, sidecars, and updates;
- fuzzing of package parsing and random-access reads;
- independent security review before a stable security claim is published.

## Security tiers

### Tier A: personal offline library (v1 target)

Authenticated encrypted packages, OS-protected library keys, optional encrypted
per-package recovery, locked key memory, in-memory playback, and signed
applications. Version 0.8 implements the package/player, locked-memory,
transactional rotation, native Windows recovery, and platform volume-adapter
source. Signed cross-platform applications, clean-host enforcement evidence on
every platform, stable runtime delivery, and independent review remain gates
for the first stable release.

### Tier B: controlled multi-device sharing

Adds an account/license service, per-device asymmetric keys, authorization,
revocation, audit events, and offline leases. The v1 envelope is versioned so
this can be added without changing encrypted media chunks.

### Tier C: commercial DRM

Requires platform DRM and hardware-backed content-decryption modules such as
Widevine, PlayReady, or FairPlay. The custom `.lrail` protection is not marketed
as a substitute.
