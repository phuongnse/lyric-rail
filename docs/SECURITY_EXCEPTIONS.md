# Security exceptions

This file records time-bounded dependency findings that cannot currently be
removed without breaking a required runtime. An exception is not a declaration
that the package is vulnerability-free.

## Python dependencies

There are no active Python dependency exceptions. The audited Windows runtime
uses PyTorch 2.13.0 with CUDA 13.0, TorchAudio 2.11.0 with CUDA 13.0,
TorchVision 0.28.0, and setuptools 83.0.0. `pip check`, CUDA import/execution,
the complete model-load smoke test, and `pip-audit` must all pass before a
runtime pack is signed. The audit also records its coverage limitation: the
local LyricRail project and PyTorch `+cu130` distributions are skipped because
they have no matching PyPI records; this is not described as proof that those
skipped distributions are vulnerability-free.

## RustSec maintenance warnings

Tauri's Linux WebKit/GTK3 dependency graph currently includes GTK3 crates marked
unmaintained plus the unsound `glib` advisory RUSTSEC-2024-0429. RustSec marks
`glib>=0.20.0` as patched, while Tauri 2.11.5 currently resolves the Linux-only
GTK3 stack to `glib 0.18.5`. `cargo tree` confirms this graph is absent from the
Windows target, but `cargo-audit` operates on the complete lockfile and therefore
still reports it. Tauri utilities also use several `unic-*` crates marked
unmaintained; these are maintenance warnings rather than published
vulnerabilities. The Linux desktop build remains blocked from stable release
until upstream Tauri/WebKit moves to a patched stack and LyricRail passes clean-
host Linux playback testing. The warnings are documented, not globally ignored.
