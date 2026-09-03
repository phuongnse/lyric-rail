# Local credentials

Everything in this directory except this README is excluded from Git and must
never enter an installer, runtime pack, support archive, or CI artifact.

Google Drive uses the desktop Picker with per-file `drive.file` access. Configure
the public OAuth client ID outside the repository. Refresh tokens are stored by
the native Player in the operating-system credential store, never in this folder
or frontend JavaScript.

Runtime signing private keys also remain outside runtime packs and source control.
The public verification key is `config/runtime-signing-public.key`. Before a
stable release, use documented offline or hardware-backed signing custody and
rehearse revocation and recovery.
