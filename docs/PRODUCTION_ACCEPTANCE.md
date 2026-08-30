# Production acceptance contract

LyricRail treats production quality as an executable gate, not a claim that every
input can be processed successfully. A job may either produce a fully accepted
artifact or stop with evidence. It must never render a guessed role, guessed word,
failed residual-vocal audit, or unpinned model.

## Process readiness path

`.process/readiness.json` selects immutable `desktop-media@1` with production as the
direction and `building` as the current stage. It is a routing map, not a second
acceptance checklist and not a production certificate:

- enforced correctness, authoritative-input, source-portability, dependency-audit,
  media-pipeline, package-security, and recovery-mechanism capabilities resolve to
  the existing frontend, Python, Rust, and conditional security profiles;
- planned capabilities retain the open stable-release work already owned by
  `SECURITY_ACCEPTANCE.md`, `SECURITY_EXCEPTIONS.md`, `RELEASE_STATUS.md`, and the
  threat model;
- ordinary development may continue while planned gaps remain, but enforced evidence
  may not regress and no release may claim production security until the normative
  gates for its claimed platform are complete.

Process adoption never upgrades the selected pack version. Moving to a later
`desktop-media` version is a separate owner-authorized consumer change, so a process
release cannot make this early-stage repository impossible to adopt or develop.

## Immutable input and model gates

- Lyric text comes only from the required UTF-8 `--lyrics` file. Caption, web lyric,
  and speech-to-text sources are disabled and cannot rewrite it.
- The job snapshots the normalized lyric text and SHA-256 in its own input directory.
- Hugging Face models are pinned to exact 40-character commits.
- Audio checkpoints are pinned to SHA-256 in `config/model-manifest.json`.
- `lyricrail doctor --production` requires all pinned snapshots and hashes to match.
- `lyricrail run` repeats the full provenance gate before downloading or processing
  the source.

## Runtime quality gates

An accepted job must pass all of these gates:

1. Source media is probed and the requested time range is valid.
2. Instrumental/vocal stems pass duration, peak, reconstruction, and energy checks.
3. Every authoritative lyric token is forced-aligned by the Vietnamese singing CTC
   model; alignment failure stops the job.
4. Residual words are checked against the exact input lyrics on both primary and
   independent consensus stems. The lexical policy is `strict`; a failed audit is
   not downgraded to a warning.
5. Singer count is selected adaptively as one or two identities. Weak evidence is
   rejected instead of being forced into two clusters.
6. Male/female display roles require unambiguous absolute-pitch evidence. Same-gender
   singers remain separate identities, so simultaneous same-lyric singing can still
   be colored as duet.
7. Duet color requires two distinct singer identities plus aligned consonant-bearing
   evidence for the authoritative words. Harmony, humming, laughter, and unrelated
   ad-libs do not satisfy that rule.
8. Unresolved role changes inside a semantic lyric group and ambiguous duet tails
   stop the job.
9. Semantic line reflow uses source phrase boundaries, sung pauses, punctuation,
   Vietnamese boundary penalties, and measured pixel width. It has no hard word-count
   target.
10. Subtitle scheduling, video/audio encoding, and final media validation must pass
    before the artifact is eligible for upload.

## Release checks

Before processing a batch:

```powershell
python scripts/install_models.py
lyricrail doctor --production
python scripts/smoke_models.py
python -m pytest -q
```

The CI suite runs on Windows, macOS, and Linux. It covers fail-closed speaker-count,
ambiguous-gender, solo, two-singer, same-gender duet, semantic reflow, lyric
immutability, model provenance, leakage cleanup, rendering, metadata, and job-state
rules. Large model inference remains a local production check because model weights
and copyrighted evaluation media are not stored in the repository.

## Model scope

The current portfolio is task-specific where a public model exists: Vietnamese song
CTC for timing and RoFormer checkpoints for instrumental, lead/backing, and residual
consensus. Public singer-diarization checkpoints are not established for this exact
Vietnamese karaoke task, so WavLM is deliberately used only as one identity feature;
pitch, stem independence, exact-word CTC, consonant evidence, semantic continuity,
and strict ambiguity gates must agree before its result is accepted.

The Vietnamese aligner is CC-BY-NC-4.0. LyricRail does not distribute model weights.
Commercial use requires replacing or separately licensing any non-commercial model
and reviewing the upstream terms for each audio checkpoint.
