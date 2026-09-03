# LyricRail release status

Status date: 2026-08-31

LyricRail is in the `building` readiness stage. This change replaces the previous
Studio/Player split with one Player plus the local karaoke/encryption core. It is not a
stable production-security release.

Implemented source behavior includes:

- one hideable Player library/queue drawer with no import autoplay;
- multiple local folder sources, deterministic media/TXT pairing and sequential work;
- authenticated local/remote range playback with dual-audio switching;
- Google Drive per-file authorization, progressive ciphertext caching and offline reuse;
- encrypted multi-source catalog and title/artist/composer/full-lyric search;
- first-line lyric thumbnails and explicit transactional local lyric revisions;
- one-time native recovery for cross-device library-key restore;
- deletion of the standalone Studio, privileged broker and online upload path.

Repository verification and independent review must be current for the exact final
snapshot before this change completes. A live Google OAuth account test additionally
requires an owner-supplied external client ID and consent configuration.

Stable blockers remain code signing and clean-host install/upgrade tests, model/runtime
delivery and licensing, macOS/Linux real-host evidence, signed updater/rollback policy,
credential and recovery drills, incident response, key custody and independent security
assessment. Legal authorization for source music/video remains the user's responsibility.
