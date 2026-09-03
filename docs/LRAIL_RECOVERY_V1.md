# LyricRail library recovery bundle v1

Status: native adapters are implemented for Windows, macOS and Linux; each claimed
release platform still requires its real-host credential and terminal drill.

## Purpose and extension

A `.lrail-recovery` file is a small, offline, passphrase-encrypted backup of the
single 256-bit device library master. It contains no song, media, package DEK,
or clear credential. It is separate from the optional per-package
`recovery-v1` key slot.

The native `lrail` process owns all passphrase input. Player opens it in a Windows
console, macOS Terminal session, or an installed Linux terminal; passphrases never enter WebView JavaScript, IPC
arguments, environment variables, logs, or filenames.

## Binary envelope

The file consists of a 32-byte fixed header followed by one canonical CBOR
document. The parser requires an exact file length and rejects symlinks,
appended bytes, truncation, non-zero reserved bytes, non-canonical CBOR, unknown
fields, and documents larger than 64 KiB.

```text
0..8    magic = "LRAILRK\\0"
8..10   little-endian schema version = 1
10..12  reserved = 0
12..16  little-endian CBOR length
16..32  reserved = 0
32..    canonical CBOR document
```

The document records the schema/version, creation time, fixed OS-vault
service/account, `argon2id-v1.3`, a random 16-byte salt, 64 MiB memory, three
iterations, one lane, a random 24-byte XChaCha nonce, SHA-256 key fingerprint,
and 48 bytes of encrypted master-key ciphertext and tag.

## Cryptography

Argon2id v1.3 derives a 256-bit KEK from the native passphrase. The library
master is encrypted with XChaCha20-Poly1305. Associated data is the domain label
`LyricRail/v1/library-recovery-bundle` plus canonical CBOR for every public
field except ciphertext. Changing KDF parameters, identity, timestamps, nonce,
or fingerprint therefore fails authentication.

The decrypted key fingerprint must also match before the candidate key may be
used. A wrong passphrase and a corrupted bundle produce the same fail-closed
key-unwrapping result.

## Restore policy

Restore is intentionally conservative:

- active master rotation is rejected;
- the chosen package library must contain at least one `.lrail` package;
- every package must fully authenticate with the recovered candidate key;
- a different current device key is never overwritten;
- after writing a previously missing credential, the native core reads it back
  and compares its fingerprint;
- official packaging and restore share the cross-process vault lock.

This means a bundle cannot silently replace a valid but different library. If
the OS key and all verified recovery material are lost, recovery is impossible.

For a new device whose songs exist only on Google Drive, Player first downloads one
selected `.lrail` object as ciphertext into a bounded recovery cache. It then launches
the same native restore command against that package. The passphrase never crosses
frontend JavaScript. After the package verifies and the key is stored, normal cloud
playback returns to progressive range reads and does not prompt again.

## Native commands

```text
lrail recovery-export --output library.lrail-recovery
lrail recovery-inspect library.lrail-recovery
lrail recovery-verify library.lrail-recovery
lrail recovery-restore library.lrail-recovery --library D:\Karaoke
```

Export asks twice; verify and restore ask once. Player invokes the same signed
native executable and leaves the console open long enough to inspect success or
failure.
