# LyricRail platform architecture

LyricRail is cross-platform at the product and protocol layers. It does not
pretend that operating-system security, credential stores, installers, media
backends, or process supervision have one portable implementation.

## Shared contracts

- `.lrail` v1 parsing, authenticated encryption, recovery, rotation, metadata,
  package limits, and random-access reads are Rust core behavior shared by all
  desktop targets.
- Studio and Player share Tauri/TypeScript UI contracts and native command
  schemas. Frontend code never receives package keys.
- The Python pipeline and signed runtime-manifest schema are platform-neutral;
  each released runtime pack must still pin native executables, libraries, and
  model artifacts for its own target triple.
- `lrail-volume-security` exposes one fail-closed result contract:
  `protected`, `unprotected`, or `unknown`. Release Studio accepts only
  `protected`.

## Native adapters

| Target | Encrypted-workspace evidence | Privilege model | Packaging boundary |
|---|---|---|---|
| Windows | Native BitLocker WMI; if access is denied, a fixed-protocol named-pipe broker authenticated against the exact SCM own-process PID | Studio remains a standard-user process; only the read-only broker runs as LocalSystem | Broker build, WiX fragment, and NSIS hooks exist only in `tauri.windows.conf.json` and `src-tauri/windows/` |
| macOS | Exact `/usr/sbin/diskutil` invocation with plist parsing; only canonical FileVault-positive APFS evidence passes | No shell and no Studio elevation | Common Tauri config; signing, hardened runtime, entitlements, notarization, and clean-host tests remain release gates |
| Linux | `/proc/self/mountinfo` resolves the containing mount; `/sys/dev/block` and recursive `slaves` inspection require an active `CRYPT-LUKS*` or `CRYPT-PLAIN*` device-mapper mapping | Unprivileged read-only kernel/sysfs inspection | Common Tauri config; package signing, clean-host tests, and the GTK/glib advisory decision remain release gates |

Unsupported, missing, malformed, timed-out, network, FUSE, container overlay, or
otherwise unverifiable volume evidence becomes `unknown`; production never
guesses that it is encrypted. Linux currently recognizes dm-crypt/LUKS, not
per-directory fscrypt. macOS crypto migration is also treated as unknown.

## Release classification

Source portability and CI compilation are not the same as a supported binary
release. As of 0.8, only Windows has a locally exercised release candidate.
macOS and Linux remain source/CI targets until their signed packages, native
credential store, volume adapter, pipeline runtime, install/upgrade/uninstall,
and playback paths pass clean-host testing on real machines.
