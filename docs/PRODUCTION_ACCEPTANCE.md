# Production acceptance

`.process/readiness.json` selects immutable `desktop-media@1`, target `production`,
and current stage `building`. It is a routing map, not a release certificate.

The enforced source floor is exercised by the `frontend`, `python`, `rust` and, for
security-sensitive changes, `security` profiles in `.process/project.json`. The current
product must preserve:

- exact user-approved lyric input and deterministic revisions;
- local-only processing with durable sequential jobs and bounded Clip Editor preview;
- authenticated package and remote-range parsing before plaintext release;
- smooth priority separation between playback and background processing/download;
- ciphertext-only cloud cache plus encrypted catalog data;
- native credential and recovery secret boundaries;
- cross-platform guarded adapters and portable path behavior.

Planned signing, clean-host, release, updater, incident, key-custody and independent
review gaps remain mandatory for a stable claim but are not silently promoted by this
source change. Large model inference and live Google account authorization remain
owner-run environment checks because neither weights nor credentials belong in source.
