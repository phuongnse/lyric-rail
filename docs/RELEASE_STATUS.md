# LyricRail 0.8 release status

Audit date: 2026-08-17

## Classification

Version 0.8 is a **private Windows release candidate**, not a stable
production-security release. The package, Studio, Player, and local Windows
runtime are implemented and exercised, but every unchecked gate in
`SECURITY_ACCEPTANCE.md` remains mandatory before a stable claim.

## Evidence completed

- 149 Python tests pass, including real FFmpeg AAC/MP3 packet-preserving remux,
  compact-output, and accurate trim fallback regressions. Production doctor
  reports zero configuration warnings and verifies all six pinned models.
- The tested Windows runtime uses Python 3.12.10, PyTorch 2.13.0+cu130,
  TorchAudio 2.11.0+cu130, TorchVision 0.28.0+cu130, and setuptools 83.0.0.
  `pip check` passes and `pip-audit --local` reports no known vulnerability
  among resolvable distributions. It explicitly skips the local LyricRail
  project and PyTorch `+cu130` distributions that have no matching PyPI record.
- Twenty-nine `.lrail` core tests cover device binding, portable recovery,
  random access, tamper, hostile metadata, parser bounds, known crypto vectors,
  locked-memory failure, ciphertext-preserving rewrap, and transactional master
  rotation. Four Player Rust tests, two Studio Rust tests, and four Player
  frontend tests also pass. The volume-security crate adds seven Windows tests;
  five Linux-native parser/device-chain tests pass under Ubuntu 24.04, and the
  macOS code/tests type-check for Apple Silicon and Intel targets.
- Coverage-guided `package_open` fuzzing completed 1,000 AddressSanitizer runs
  under Ubuntu 24.04/WSL2 with pinned Rust nightly 2026-07-01 and cargo-fuzz
  0.13.2: no crash, final coverage 2,166, final corpus 43 inputs.
- Player release QA authenticates a generated package, seeks, replays after end,
  renders timed lyrics, and plays both switchable `Karaoke` and
  `Original Reference` tracks without creating a clear playback file.
- Native recovery export/verify/restore and resumable library-key rotation keep
  passphrases and keys out of frontend JavaScript. Media ciphertext remains
  byte-identical during master-key rotation.
- Windows release Studio verifies the complete signed runtime before launch and
  surfaces native BitLocker evidence. A read-only LocalSystem broker now uses a
  fixed 32-byte request/40-byte response protocol, rejects remote pipes, and is
  authenticated by matching the pipe-server PID to the exact running SCM
  own-process service. Both WiX and NSIS bundles include service installation;
  signing and clean-host standard-account testing remain open gates.
- The shared volume-security contract now has native adapters for macOS
  APFS/FileVault and Linux mountinfo/sysfs dm-crypt/LUKS. Windows-specific build
  hooks exist only in `tauri.windows.conf.json`. Linux tests run natively; both
  macOS architectures type-check, but real-Mac/Linux runtime evidence is not yet
  sufficient for a supported release.
- The private Windows RC2 runtime was assembled and independently verified:
  56,435 signed payload files, 17,097,784,081 payload bytes, manifest SHA-256
  `0cdd738f6f74f864b8fb143f13b001ae219055217b5425e78dead8d4f2a99824`,
  key ID `5588062a9fe343d44535cf651382ef21`. Doctor and all model-load smoke tests pass
  on an RTX 4070/CUDA 13.0 host.
- Strict Rust clippy, npm build/test, npm audit, Python audit, RustSec audit, and
  the five CycloneDX 1.5 SBOMs completed. The current Studio-Rust SBOM contains
  329 components and includes the volume-security/broker dependency graph; the
  other release BOMs remain current for their unchanged scopes.
- RustSec reports no vulnerability at the configured fail severity. Its 17
  allowed warnings include a Linux-only Tauri GTK3/glib 0.18.5 chain plus
  unmaintained transitive crates; the glib advisory is not in the resolved
  `x86_64-pc-windows-msvc` graph, but remains a Linux release blocker.
- Fresh unsigned Player and Studio-shell NSIS/MSI installers, diagnostic EXEs,
  and verified SHA-256 checksums are in `release/windows`. All six report
  `NotSigned`; WiX ICE validation remained enabled.

## Stable-release blockers

1. Obtain an Authenticode certificate, sign every Windows executable and
   installer, and test clean install, file association, uninstall, and upgrade
   on every supported Windows version.
2. Replace the RC signing seed with documented offline or hardware-backed key
   custody. Resolve model/checkpoint redistribution licenses, decide how the
   17.1 GB runtime is delivered, and clean-host test the signed runtime plus
   Studio installer. The current Studio installer does not embed RC2.
3. Sign and clean-host test the least-privilege Windows BitLocker broker through
   clean install, upgrade, uninstall, service failure, and standard-account
   enforcement. Runtime-test the implemented macOS FileVault and Linux
   dm-crypt/LUKS adapters on supported real machines and filesystem layouts.
4. Build, sign, notarize, and clean-host test macOS. Build, sign, and clean-host
   test Linux, including resolution of the Tauri GTK/glib maintenance and
   unsoundness advisory chain.
5. Clean-host test credential storage and the recovery console on every
   supported platform; rehearse destructive key-loss and recovery procedures.
6. Define a signed updater and rollback policy, protected release metadata, and
   signing-key compromise/recovery procedures.
7. Complete independent review of the format, key lifecycle, parser boundary,
   playback protocol, runtime pack, privileged broker, and update chain.

Legal authorization for packaged music/video is outside the security claim.
LyricRail stores truthful private-use/no-license metadata but cannot grant rights
the user does not own.
