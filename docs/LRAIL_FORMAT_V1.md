# LyricRail package format v1

Status: normative design. Multi-byte integers are unsigned little-endian.

## Identity

- extension: `.lrail`
- display name: `LyricRail Karaoke Package`
- MIME type: `application/vnd.lyricrail.package`
- Windows ProgID: `LyricRail.Package`
- format identifier / UTI: `com.lyricrail.package`
- eight-byte magic: `LRAIL\r\n\x1a`
- major version: `1`

The extension is cosmetic. A Player accepts a package only after validating its
magic, header, supported version, bounds, key envelope, and manifest tag.

## Goals

- authenticated encryption of every private byte;
- random access suitable for media read/seek callbacks;
- custom audio labels such as `Karaoke` and `Original Reference`;
- dynamic lyrics and role styling independent of the video encode;
- negligible cryptographic overhead for large media;
- forward-compatible key envelopes and schema migration;
- deterministic rejection of corrupted or hostile packages.

## High-level layout

```text
fixed header (128 bytes)
key-envelope document (canonical CBOR, clear but authenticated)
encrypted asset chunks
encrypted canonical-CBOR manifest
```

Only the fixed header and key-envelope document are clear. They contain no song
title, source URL, artwork, lyric, or media metadata.

## Fixed header

The 128-byte header contains:

| Field | Size |
| --- | ---: |
| magic | 8 |
| format major | 2 |
| format minor | 2 |
| flags | 4 |
| package UUID | 16 |
| envelope offset | 8 |
| envelope length | 8 |
| manifest offset | 8 |
| manifest ciphertext length | 8 |
| manifest nonce | 24 |
| declared package length | 8 |
| reserved, all zero in v1 | 32 |

Hard limits are checked before any seek or allocation. Unknown non-zero reserved
bytes or unknown mandatory flags cause rejection.

## Cryptography

- asset and manifest algorithm: XChaCha20-Poly1305-IETF;
- data-encryption key (DEK): 32 random bytes per package;
- nonce: 24 random bytes per encrypted object, unique within that DEK;
- authentication tag: 16 bytes, supplied by the AEAD construction;
- default plaintext chunk size: 1 MiB;
- maximum plaintext chunk size: 8 MiB;
- maximum package size: 256 GiB in v1.

Each chunk uses associated data encoded without ambiguity:

```text
"LyricRail/v1/chunk" || package_uuid || asset_uuid || u64(chunk_index)
|| u64(plaintext_offset) || u32(plaintext_length) || content_encoding
```

The encrypted manifest uses the exact final fixed header plus the complete clear
key-envelope bytes as associated data. Header or envelope tampering therefore
causes manifest authentication to fail.

## Key envelopes

The envelope is canonical CBOR with one or more key slots. Every slot wraps the
same package DEK and declares a versioned mechanism.

Required v1 mechanisms:

- `os-vault-v1`: XChaCha20-Poly1305 wrapping under a per-package KEK derived by
  HKDF-SHA-256 from the OS-stored library master, package UUID, and a random
  non-secret 128-bit key ID. A package may temporarily contain multiple
  `os-vault-v1` slots during transactional master rotation; readers try every
  compatible slot. Successful rotation returns the package to one vault slot.
- `recovery-v1`: optional wrapping under a KEK derived from a user recovery
  passphrase with Argon2id. The slot records salt and calibrated parameters, not
  the passphrase or KEK.
- `test-v1`: test fixtures only and unconditionally rejected by release builds.

No key is stored next to the package in plaintext. CLI arguments and environment
variables are not accepted as release key material.

## Manifest

The authenticated, encrypted manifest commits to:

- schema and minimum Player versions;
- package UUID and creation timestamp;
- complete metadata, credits, sources, and rights statements;
- audio track names, roles, languages, codecs, and default selection;
- video and artwork descriptors;
- authoritative lyrics, timing, and singer-role asset IDs;
- every asset ID, media type, plaintext length, content encoding, and SHA-256;
- every chunk index, plaintext range, file offset, ciphertext length, and nonce;
- exact asset and chunk counts;
- producer application identity and build version.

An asset is valid only if its chunks form a contiguous, non-overlapping sequence
from offset zero to the declared plaintext length. The Player rejects missing,
duplicate, overlapping, reordered, out-of-bounds, or trailing undeclared data.

## Assets and compression

Version 1 currently supports only `identity`. Media, JSON, and image bytes enter
the package unchanged; compression decisions belong to the Studio media layer.
Readers reject every unknown content-encoding identifier. A later format minor
may add compression only together with explicit output-size and ratio limits.

Recommended v1 logical assets:

```text
media/video.mp4                    silent playback picture
audio/karaoke.m4a                  default instrumental playback audio
audio/original-reference.m4a       AAC reference audio (packet-copy when eligible)
audio/original-reference.mp3       MP3 reference audio (packet-copy when eligible)
lyrics/timing.json                 syllable timing and singer roles
lyrics/render-plan.json            dynamic two-line presentation schedule
metadata/release.json              professional credits and source links
presentation/template.json         role colors and layout policy
sources/visual-license.json        selected asset pages/licenses, when present
```

Exactly one of the two `audio/original-reference.*` variants is present. The
Player accepts only the exact logical-name/media-type pairs shown above and
rejects missing or ambiguous variants. The video has no burned lyrics or
embedded audio. The Player runs the selected encrypted audio asset as the timing
master and synchronizes the video to it. A rendered fallback is not included
because it would duplicate media and conflict with the minimum-size objective.

## Parsing limits

Release builds enforce at least:

- envelope: 1 MiB;
- encrypted manifest: 32 MiB;
- assets: 128;
- chunks: 1,000,000;
- metadata strings: 1 MiB each and valid UTF-8 where declared;
- no filesystem paths supplied by a package;
- checked integer arithmetic for every offset and length;
- no reads outside the declared package length;
- no plaintext from a chunk before tag verification.

## Versioning

Readers support a major version explicitly or reject it. Minor versions may add
optional fields only. Cryptographic algorithms, envelope mechanisms, and content
encodings use independent identifiers so they can be migrated without ambiguity.
