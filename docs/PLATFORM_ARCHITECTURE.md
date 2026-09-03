# Platform architecture

LyricRail has one Tauri Player, one Rust package/security core and one local Python
processing worker. Paths and command arguments stay native arrays; no platform uses a
shell to launch media, recovery or model work.

| Boundary | Portable contract | Native adapter |
|---|---|---|
| Package playback | One authenticated random-access reader for local and remote bytes | Local file handle or bounded provider cache |
| Karaoke presentation | Authenticated typed template plus renderer-independent slot/cue schedule; exact role colors and reference geometry | Native bounded parser; responsive WebView container-unit renderer with shipped font |
| Processing | One low-priority sequential worker with isolated jobs and an OS-released per-job run lease | Windows Job Object/priority class; Unix process priority and native file locking |
| Keys/tokens | Secrets never cross frontend JavaScript | Credential Manager, Keychain or Secret Service via `keyring` |
| Recovery prompt | Passphrase is owned by the native `lrail` executable | Windows console, macOS Terminal/AppleScript and Linux terminal adapters; real-host release evidence remains gated |
| Google OAuth | Desktop Picker, PKCE, state and loopback callback | System browser plus loopback listener |
| Local clip preview | One identity-bound regular media path plus optional Start/End values enters the existing worker without source mutation | Guarded file identity, bounded ffprobe/ffmpeg, anonymous portable PCM handle and positional 2 MiB range reads |
| Tasks and output | Stable IDs, one state machine, measured-only ETA, sequenced snapshots/events and bounded redacted output | Native 100 ms event batching; virtualized active-only Activity Tasks view and inline Issue output |
| Issues and resolution | Stable bounded issue codes, generic related task IDs, linked item IDs, one contextual action home and closed resolution kinds | Styled Activity Issues tab; native pinned-model subprocess only for unsigned development roots; minimal macOS application menu |
| Cache | Versioned ciphertext blocks with hard byte/file limits, at most eight foreground writers plus one background writer, and bounded-memory eviction | App cache directory, process-session partial ownership and portable file operations |

The task contract is platform-neutral. Windows Job Objects, Unix process groups,
credential adapters and native paths remain guarded adapters; stage weighting,
sequencing, limits, redaction, UI behavior and retry logic are shared across operating
systems.

Automatic vocal roles remain evidence-bound. A low-margin clause can use an accepted
full-group speaker embedding, or a multi-line group whose aggregate pitch agrees with
the nearest known speaker cluster, has no opposing classified line pitch and meets the
configured coverage ratio. Raw containing-line roles are usable only when every line
agrees and independently meets its margin. A mixed display split is normalized only
when valid semantic clauses cover the entire authoritative group and unanimously own
one solo role; independently proven co-lead evidence alone may produce `duet`. All other
ambiguity fails before rendering. Named completed checkpoints expose progress through
the long role-analysis stage.

The dynamic Player does not own a second visual theme. Native package open validates the
required presentation asset's kind, media type, enums, colors and numeric bounds, then
returns only normalized fields. The WebView scales reference pixels to its video-stage
container, keeps top-left and bottom-right slots distinct, layers inner/outer outlines,
and animates only render-plan cue events across their authenticated lead interval.

OS-native paths remain exact `Path`/argument values inside the local worker. Internal
job JSON uses ASCII escapes so an OS string that contains a lone UTF-16 surrogate still
round-trips through valid UTF-8 JSON without changing the path. Such code units are
replaced only when data crosses a diagnostic, log, frontend or portable display-label
boundary. Worker control IDs and package paths must already contain valid Unicode and
are preserved exactly; invalid controls become a failed worker event instead of a
different path. Portable title/artist/composer/filename labels replace invalid code
units before any package path is created. Authoritative lyric snapshots stay strict UTF-8 bytes and retain their exact
text/hash; they never use the diagnostic sanitizer.

The native argument array enables CPython UTF-8 mode before module execution on every
platform and also fixes UTF-8 stdio for the non-isolated development worker. Development
stdin/stdout are strict; stderr retains CPython's safe backslash-replacement error policy
while emitting valid Unicode as strict UTF-8 bytes.
Each reader carries the monotonically unique generation of the process that owns it.
Closing current-worker stdout without a terminal event atomically fails only the active
task, retains its bounded diagnostics and starts the next queued task through the same
shared dispatcher; events, stderr and EOF from an older generation are ignored.
Structured-event generation check and its worker/request transition share the scheduler
mutex, so replacement cannot enter between validation and mutation. A normal terminal
event clears the active ID first, so current-generation EOF cannot report it twice.

The removed Studio, privileged volume broker and encrypted-workspace adapters are not
supported compatibility surfaces. Processing intermediates remain cleartext inside the
current isolated job; cleanup is scoped to that authenticated completed job and never
claims SSD secure erasure.

Windows is the current private release-candidate target. macOS and Linux compile/test
targets still require real-host credential, recovery-terminal, media and release
evidence before they can be claimed as supported releases.

Windows development uses `scripts/bootstrap_windows.ps1` and
`scripts/windows_dev_environment.ps1` on a native Windows filesystem. Official tools
come from versioned winget package declarations, Python dependencies live in the repo
`.venv`, and Cargo output is isolated in `.dev/target-windows`; the workflow never
invokes or requires WSL. Model weights remain an explicit optional download because
their size and redistribution licensing are separate readiness concerns.

Windows Smart App Control enforcement is a host-policy boundary, not another platform
implementation. It blocks unsigned local Rust build helpers and development binaries;
bootstrap detects it before mutation and does not disable, bypass, or relocate output
to evade the policy. Developers must use a Windows host policy that permits local
builds. Product behavior and verification profiles remain shared across platforms.

The Windows adapter validates the repository venv identity before reuse. Acceleration
selection changes only native dependency artifacts: CPU and NVIDIA ONNX distributions
cannot coexist, NVIDIA uses the pinned official PyTorch CUDA index, and the bootstrap
must observe a CUDA device plus ONNX CUDA provider before reporting that backend.
