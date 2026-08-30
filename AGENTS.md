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

For non-trivial delivery work, enter through the managed run-change skill and follow
the processctl lifecycle: start, plan, implement, verify, independent review, finish.

This repository owns product decisions, domain rules, exact argument-array commands,
merge policy, and release authority. The process owns only lifecycle transitions,
managed skills, evidence freshness, and rejection of self-review.

Do not edit .agents/skills or .process/adopt-process.py by hand. They are replaced by
the hash-locked engineering-process adoption in a dependency pull request.
<!-- engineering-process:end -->
