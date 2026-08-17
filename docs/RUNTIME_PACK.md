# LyricRail Studio runtime packs

Status: the private Windows x64 RC2 runtime is assembled and verified locally.
It is not a redistributable stable payload; license review, stable signing-key
custody, delivery design, and clean-host testing remain release gates.

## Trust model

Release Studio does not trust `LYRICRAIL_HOME` merely because it contains a
configuration file. A runtime root must contain both:

```text
runtime-manifest.json
runtime-manifest.sig
```

The signature is Ed25519 over the exact manifest bytes with the domain prefix
`LyricRail runtime manifest v1`. Studio embeds the expected public key and uses
strict signature verification. The signed manifest binds:

- runtime schema, LyricRail version, and exact OS/architecture;
- relative paths for Python, FFmpeg, ffprobe, and the native `lrail` packager;
- every other regular file by normalized relative path, byte length, and
  SHA-256.

Verification walks the complete root and rejects missing, changed, or extra
files; duplicate/unsorted paths; absolute or traversing paths; symlinks and
Windows reparse points; special files; oversized manifests/files/inventories;
wrong version/platform/key; incomplete signature pairs; mutable/secret root
directories; and root `.env*` files. The four executable paths must be distinct
and covered by the same inventory.

Debug Studio may use an unsigned source checkout, but reports
`development-unverified` in the UI. That exception is compiled out of release
builds. Runtime verification is repeated immediately before process launch so a
status check is not treated as authorization.

## Immutable runtime and mutable data

Signed files are read-only runtime material. Studio sets
`LYRICRAIL_DATA_HOME` to its platform application-data directory and keeps these
trees outside the pack:

```text
input/  output/  cache/  logs/  credentials/
```

The signed Python process runs with user site packages disabled, bytecode writes
disabled, exact tool environment paths, and isolated mode in release. This
prevents an unsigned current directory, `PYTHONPATH`, or user site-package tree
from being inserted ahead of the signed runtime.

## Reproducible Windows pack build

The builder creates a unique sibling staging directory, refuses to overwrite an
existing destination, rejects links/reparse points/special files, runs the
production doctor and model-load smoke tests with mutable cache outside the
signed root, signs the exhaustive manifest, verifies it using only the public
key, and then atomically publishes the finished directory.

```text
.venv\Scripts\python.exe scripts\build_windows_runtime_pack.py \
  --output release\runtime\LyricRail-Runtime-0.8.0-windows-x86_64-rc2 \
  --private-key credentials\runtime-signing-private.key \
  --public-key config\runtime-signing-public.key
```

Current evidence:

```text
report: release/runtime/LyricRail-Runtime-0.8.0-windows-x86_64-rc2.build-report.json
payload files: 56,435
payload bytes: 17,097,784,081
manifest SHA-256: 0cdd738f6f74f864b8fb143f13b001ae219055217b5425e78dead8d4f2a99824
key ID: 5588062a9fe343d44535cf651382ef21
doctor: ready
models: forced aligner, speaker embedder, and four separators loaded
```

Independent verification can be repeated without the private key:

```text
lrail runtime-verify \
  --root release\runtime\LyricRail-Runtime-0.8.0-windows-x86_64-rc2 \
  --public-key config\runtime-signing-public.key
```

Manifest creation is deliberately create-once and refuses to overwrite an
existing manifest/signature pair. Rebuild a clean staging root for every
release; do not mutate and re-sign a previously published pack.

## Limitations and release custody

This mechanism authenticates runtime contents; it does not replace signed OS
installers, Authenticode/notarization, protected update metadata, encrypted
workspace enforcement, or clean-host testing. The current Windows installer is
a Studio shell and does not embed the 17.1 GB RC2 pack.

The development/RC private seed under `credentials/` is not an acceptable
stable-release custody arrangement. Stable signing must use an offline or
hardware-backed process with documented backup, compromise, rotation, and
recovery procedures. Every bundled model/checkpoint license must be approved
before the pack is redistributed.
