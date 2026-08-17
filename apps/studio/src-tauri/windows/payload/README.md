# Generated Volume Broker payload

`npm run prepare:broker --workspace @lyricrail/studio` builds the locked release
binary and writes `lyricrail-volume-broker.exe` here immediately before Tauri's
bundle phase. The executable is ignored by source control.

Stable release automation must Authenticode-sign and verify this binary before
the enclosing Studio installer is signed. The private Windows RC intentionally
keeps the generated broker and installer marked unsigned.
