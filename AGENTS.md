# LyricRail repository instructions

LyricRail is a private cross-platform karaoke production and playback system.

- Keep orchestration, path handling, and package behavior portable across Windows,
  macOS, and Linux. Isolate native behavior behind guarded platform adapters.
- Treat supplied UTF-8 lyrics as immutable. Models may align the exact words and
  classify roles, but must never infer, replace, or correct lyric text.
- Never commit source media, generated packages, model weights, runtime bundles,
  logs, credentials, tokens, or production-only metadata.
- Preserve source media and unrelated workspace files. Cleanup must remain scoped
  to one explicitly completed and fully authenticated job.
- Treat package-format, cryptography, key lifecycle, recovery, parser, playback,
  runtime-signing, privileged-broker, and volume-security changes as
  security-sensitive. Their change contracts must include the `security` profile.
- Keep package-format and recovery behavior synchronized with their specifications,
  known-answer fixtures, compatibility tests, and threat-model documentation.
- Do not describe a release as production-grade security while a required gate in
  `docs/SECURITY_ACCEPTANCE.md` remains open for a claimed platform.
- Use the profiles in `.process/project.json` as the canonical local verification
  commands. CI may add platform and release evidence but must not weaken them.

<!-- engineering-process:start -->
## Engineering process

Use the portable skills pinned by `.process/process.lock` for every non-trivial
change. Enter through `run-change` and use `processctl change ...` for specification,
planning, implementation registration, checkpoint verification, independent review,
finding resolution, and completion.

The project owns product decisions, domain contracts, exact verification commands,
and publication authority. The process distribution owns lifecycle semantics and
managed skills. Do not edit managed skills in this repository; update the pinned
distribution and synchronize them instead.

Independent review requires an attested read-only actor and context that did not
implement the current cycle. No particular agent host is required. Missing or stale
evidence, self-review, and publication without separate authorization are blocking.
<!-- engineering-process:end -->
