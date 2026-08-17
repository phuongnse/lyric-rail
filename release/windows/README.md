# LyricRail 0.8.0 Windows release-candidate artifacts

The current EXE, NSIS, and MSI files were built on 2026-08-17 for Windows x64.

- `LyricRail-Player_0.8.0_x64-unsigned-setup.exe` is the NSIS Player installer.
  It registers `.lrail`, enforces a single Player instance, and is functionally
  self-contained apart from the Windows WebView2 runtime supplied by Tauri.
- `LyricRail-Player_0.8.0_x64-unsigned.msi` is the equivalent WiX/MSI Player
  installer for managed Windows deployment testing.
- `LyricRail-Studio_0.8.0_x64-unsigned-shell-setup.exe` installs the native
  Studio shell plus the read-only `LyricRailVolumeBroker` Windows service. The
  NSIS installer is per-machine because the broker runs as an isolated
  LocalSystem own-process service; Studio itself remains unelevated. It
  intentionally does not bundle the multi-gigabyte Python, CUDA, FFmpeg, and
  model runtime. A release build refuses to start jobs unless
  `LYRICRAIL_HOME` points to a complete runtime pack covered by a valid
  exhaustive Ed25519 manifest signed by the embedded project key and the native
  workspace-volume protection check succeeds.
- `LyricRail-Studio_0.8.0_x64-unsigned-shell.msi` is the equivalent WiX/MSI
  Studio-shell plus service installer for managed Windows deployment testing.
- The two non-setup executables are unpackaged build outputs for diagnostics.
- `SHA256SUMS.txt` records SHA-256 for all six current artifacts.
- The separately verified 17.1 GB Windows RC2 runtime is under
  `release/runtime/LyricRail-Runtime-0.8.0-windows-x86_64-rc2`; it is not
  embedded in either Studio installer and is not cleared for redistribution.
- `stale-20260816/` retains the previous MSI pair only for build traceability;
  those files are not part of the current artifact set.

All current artifacts are deliberately named `unsigned`.
`Get-AuthenticodeSignature` reported `NotSigned` for every EXE and MSI; do not
redistribute them as a stable trusted release. Authenticode signing and
clean-machine installer tests remain release gates.
