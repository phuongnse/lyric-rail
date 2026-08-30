---
name: verify-change
description: Select and run project-owned verification profiles, preserve exact checkpoint evidence, and advance a planned change only when all required profiles pass. Use after implementation, after review fixes, or before an independent-review readiness claim.
---

# Verify a Change

## Goal

Produce current, reproducible evidence for the acceptance outcomes affected by the
change while avoiding unrelated broad verification.

## Workflow

1. Read the lifecycle state, change contract, nearest AGENTS.md, and
   .process/project.json.
2. Map each affected acceptance outcome to the smallest project-owned profile that
   proves it. Reuse still-current evidence and expand scope only when invalidation or
   project policy requires it.
3. Use processctl change verify for local lifecycle evidence. When the change selects
   project-owned `requiredEvidence`, create its exact request with `processctl change
   remote request`, let the project adapter obtain every declared selector bundle,
   and bind the complete set with `processctl change remote ingest`. The lifecycle
   remains implementing until both local profiles and remote evidence pass. Use
   processctl verify only for exploratory or focused evidence that is not intended to
   advance the lifecycle.
   Do not substitute a different command, runtime, trust boundary, or environment
   when a required check is blocked.
   A registered authority transition supplies the same explicit `--candidate-root`
   to local verification, remote request and remote ingest. The CLI and lifecycle
   state stay in the synchronized N-1 control workspace; transition request and
   candidate evidence must already be ingested. Never run the target CLI as the
   lifecycle authority merely because the candidate lock names it.
4. Treat a process-owned command as successful only when its execution boundary
   passes and its complete admitted stdout and stderr contain no classified warning
   or error diagnostic. Exit zero does not override diagnostic failure. Correct the
   output at its owning boundary; do not suppress it or replace the canonical command.
5. Bind local and remote evidence to the same current repository checkpoint,
   comparison base, workspace fingerprint, and immutable workflow checkpoint.
   Any relevant edit or source mutation during verification invalidates the evidence.
   Require bounded correlation and output metadata without copying raw secrets into
   lifecycle artifacts; exercise cleanup and resource limits affected by the change.
6. Report failed, timed-out, missing, and blocked checks exactly; never convert them
   into a pass or an implicit deferral.

## Hard gates

- Project manifests own commands; this skill must not invent replacement commands.
- A focused check cannot prove a broader acceptance boundary than it exercises.
- A provider job conclusion, branch, PR check, or unbound artifact cannot substitute
  for the exact project requirement and ingested supplemental evidence set.
- Verification does not publish, merge, deploy, or approve a change.

## Output

Return change id, cycle, profiles, checkpoint, check statuses, evidence report
locations, lifecycle phase, gaps, and the next owner.
