# LyricRail key management

Status: implemented Windows release-candidate behavior plus explicit
stable-release gaps.

## Implemented hierarchy

```text
platform credential store
        │
        │  256-bit library master
        ▼
HKDF-SHA-256(package UUID, random 128-bit key ID, domain label)
        │
        │  per-package key-encryption key (KEK)
        ▼
XChaCha20-Poly1305 wrapped package data-encryption key (DEK)
        │
        ▼
encrypted manifest and authenticated asset chunks
```

- The library master uses the fixed service/account identifiers
  `com.lyricrail.keys` / `library-master-v1`; it is created from the OS CSPRNG.
- The keyring backend is Windows Credential Manager, macOS Keychain, or Linux
  Secret Service as supplied by the maintained Rust `keyring` crate.
- There is no filesystem or environment-variable fallback. An unavailable store
  is a blocking error.
- Every package gets a new random 256-bit DEK, package UUID, key ID, manifest
  nonce, wrap nonce, and chunk nonces.
- DEKs and derived KEKs use zeroizing buffers in the Rust core. Content keys are
  never returned to frontend JavaScript or placed in command arguments/logs.
- Application-owned vault masters, DEKs, derived KEKs, recovery passphrases, and
  runtime signing-key text are held in fixed-address memory locked with `mlock`
  on Unix or `VirtualLock` on Windows. Buffers are zeroized before unlock. If
  the operating system refuses the lock, the operation fails closed rather than
  falling back to pageable key memory.
- Hardware-backed or non-exportable storage is **not** claimed by version 0.8;
  platform credential-store policy controls the protection of the library
  master.

## Recovery slot

The native `lrail` CLI can add a second `recovery-v1` slot:

```text
lrail pack --request package-request.json --output song.lrail --with-recovery
lrail verify song.lrail --recovery
```

The passphrase is read from the native terminal without echo and is never passed
on the command line. Argon2id v1.3 uses a random 16-byte salt, 64 MiB memory,
three iterations, and one lane, then XChaCha20-Poly1305 wraps the same package
DEK. A minimum of 12 UTF-8 bytes is enforced; users should use a substantially
longer unique passphrase.

Recovery material is self-contained in the authenticated package envelope. The
passphrase itself is the user's recovery secret. Losing both the active OS
credential and every recovery passphrase is irreversible.

Studio 0.8 creates OS-vault packages by default. A recovery-passphrase dialog is
not rendered inside the WebView because secrets may not cross the JavaScript
boundary; recovery-enabled per-package packaging currently uses the native CLI.

The CLI locks both recovery-passphrase entries before comparison/use. It also
locks runtime-signing private-key text during offline manifest signing. These
controls protect application-managed buffers from normal paging; they do not
claim protection from an administrator, debugger, process dump, or compromised
kernel.

## Offline library recovery bundle

Studio exposes Export, Verify, and Restore actions that launch the signed native
`lrail` executable in a separate console. The native process reads the
passphrase without echo; only selected filesystem paths cross frontend IPC.

The `.lrail-recovery` v1 format encrypts the 256-bit library master with
Argon2id v1.3 (random 16-byte salt, 64 MiB, three iterations, one lane) and
XChaCha20-Poly1305. All public metadata is associated data. Restore refuses to
write the OS vault unless at least one package exists and the entire selected
library fully verifies with the recovered candidate. It never overwrites a
different current key and is blocked during rotation. See
`docs/LRAIL_RECOVERY_V1.md` for the normative layout and loss behavior.

## Transactional library-master rotation

Studio exposes a native folder-scoped rotation workflow. The selected library is
inventoried before the operation starts; every package must authenticate with
the current device key. Official device-vault packaging and rotation share a
cross-process lock, so a new package cannot be committed under a stale master
while the library is changing.

Rotation uses three OS credential-store accounts temporarily:

```text
library-master-v1                current key
library-master-v1-rotation-old   durable rollback/opening copy
library-master-v1-rotation-new   pending new key
```

The native sequence is:

1. Rewrap every package with both old and new `os-vault-v1` slots.
2. Fully verify every package under each key.
3. Switch `library-master-v1` to the new key.
4. Rewrap every package to one new-key slot and fully verify it again.
5. Delete the temporary old/new credential entries only after all packages pass.

An append-only, `sync_all` journal contains package IDs, normalized relative
paths, transition events, and SHA-256 key fingerprints; it never contains key
bytes. Each package replacement is authenticated before and after an atomic
same-directory commit. Windows uses `ReplaceFileW` with a recovery backup; Unix
uses a same-filesystem hard link plus atomic rename. A crash before or after any
commit resumes from the journal and reconciles the replacement/backup sidecars.

Only the small key envelope, authenticated manifest, header, and their offsets
change. Encrypted asset ciphertext is streamed byte-for-byte, so rotation does
not re-encode media, reduce input quality, or leave a second media copy inside
the final `.lrail` output. Successful cleanup returns every package to exactly
one device-vault slot, minimizing permanent overhead.

## Not implemented yet

The following are stable-release gates, not current capabilities:

- credential-store access conditioned on device biometric/user presence;
- device authorization/revocation or a multi-device key service;
- secure crash-dump exclusion verified on every supported platform.

Version 0.8 therefore targets a single-user personal offline library. It must
not be marketed as commercial DRM or hardware-backed key custody.
