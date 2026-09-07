# LyricRail local-core runtime packs

Status: runtime verification is implemented, but the simplified Player requires a
freshly built and verified pack. No prior Studio runtime is release evidence for this
snapshot. License review, stable signing-key custody, delivery design and clean-host
testing remain release gates.

## Trust model

Release Player does not trust `LYRICRAIL_HOME` merely because it contains a
configuration file. A runtime root must contain both:

```text
runtime-manifest.json
runtime-manifest.sig
```

The signature is Ed25519 over the exact manifest bytes with the domain prefix
`LyricRail runtime manifest v1`. Player embeds the expected public key and uses
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

Debug Player may use an unsigned source checkout, but reports
`development-unverified` in the UI. That exception is compiled out of release
builds. Runtime verification is repeated immediately before process launch so a
status check is not treated as authorization.

When pinned models are absent in an unsigned development checkout, the Player may run
only the canonical `scripts/install_models.py` argument array after explicit size and
upstream-license confirmation. Every audio checkpoint/config download has one manifest-
owned HTTPS release URL, exact byte length and SHA-256. The installer streams into a
unique sibling temporary file, enforces the declared/received byte bound, hashes while
writing and atomically replaces a missing or invalid cache entry only after verification.
An interrupted partial therefore remains retryable without being mistaken for a valid
cache hit. Output is bounded, cancellation is supported and final full-manifest model
provenance must pass before setup-required jobs retry. This resolver
publishes through the same native Activity task/output contract as processing and
refuses `signed-verified` roots; a missing signed component requires a replacement pack.

## Immutable runtime and mutable data

Signed files are read-only runtime material. Player sets
`LYRICRAIL_DATA_HOME` to its platform application-data directory and keeps these
trees outside the pack:

```text
input/  output/  cache/  logs/  credentials/
```

The signed Python process runs with user site packages disabled, bytecode writes
disabled, exact tool environment paths, and isolated mode in release. This
prevents an unsigned current directory, `PYTHONPATH`, or user site-package tree
from being inserted ahead of the signed runtime.

Runtime command diagnostics record the executable name and each argument as data. The
shared native output boundary removes private paths, remote signed/query values and
credential-shaped values before bounded storage, events or clipboard copy; it never
logs environment secrets.

## Reproducible Windows pack build

Prepare the native Windows development host first. This installs official toolchains,
repo dependencies and pinned models without WSL or portable root-level Python folders:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\bootstrap_windows.ps1 -IncludeModels
```

Development bootstrap is not a production-signing action. Runtime signing keys remain
external and every release-acceptance gate below still applies.

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

Independent verification of a newly built pack uses only the public key:

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
a Player bundle and does not embed the separately delivered model/runtime pack.

The development/RC private seed under `credentials/` is not an acceptable
stable-release custody arrangement. Stable signing must use an offline or
hardware-backed process with documented backup, compromise, rotation, and
recovery procedures. Every bundled model/checkpoint license must be approved
before the pack is redistributed.
The development resolver does not change that redistribution or release-acceptance gap.
