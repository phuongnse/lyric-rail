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
3. Use processctl change verify for lifecycle evidence. Use processctl verify only
   for exploratory or focused evidence that is not intended to advance the lifecycle.
   Do not substitute a different command, runtime, trust boundary, or environment
   when a required check is blocked.
4. Bind evidence to the current repository checkpoint and workspace fingerprint.
   Any relevant edit or source mutation during verification invalidates the evidence.
   Require bounded correlation and output metadata without copying raw secrets into
   lifecycle artifacts; exercise cleanup and resource limits affected by the change.
5. Report failed, timed-out, missing, and blocked checks exactly; never convert them
   into a pass or an implicit deferral.

## Hard gates

- Project manifests own commands; this skill must not invent replacement commands.
- A focused check cannot prove a broader acceptance boundary than it exercises.
- Verification does not publish, merge, deploy, or approve a change.

## Output

Return change id, cycle, profiles, checkpoint, check statuses, evidence report
locations, lifecycle phase, gaps, and the next owner.
