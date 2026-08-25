---
name: run-change
description: Orchestrate a non-trivial engineering change through specification, planning, implementation, checkpoint verification, independent review, finding resolution, and completion. Use as the default entry skill whenever a project change must be delivered rather than merely explained or diagnosed.
---

# Run a Change

## Goal

Drive one canonical lifecycle from request to auditable completion while project
owners retain product decisions, domain policy, commands, and publication authority.

## Required reference

Read [references/execution.md](references/execution.md) completely before starting or
resuming a change. It defines universal execution, blocker, evidence, and delegation
semantics shared by every project.

## Lifecycle

1. Run processctl doctor and inspect processctl change status when a change id exists.
2. Route the current phase without creating a parallel plan or checklist:
   - no registered change: use define-change-contract;
   - specified: use plan-change;
   - planned or changes-requested: use implement-change;
   - implementing: use verify-change after a clean immutable checkpoint exists;
   - verified: obtain a separate reviewer through review-change;
   - review-pending: wait for the assigned reviewer; do not self-review;
   - approved: use finish-change;
   - completed: stop unless publication was separately authorized.
3. Apply the nearest AGENTS.md and domain skill inside each phase. Project policy may
   add stronger gates but cannot remove lifecycle phases, baseline profiles,
   independent review, evidence freshness, or finding closure.
4. When a command, gate, release, adoption, or external integration fails, apply the
   failure-to-invariant protocol in the required execution reference before any
   corrective mutation. Keep dependent candidates blocked until the owning boundary
   and required proof are explicit.
5. After a valid finding, preserve the reviewed checkpoint, begin the next
   implementation cycle, resolve the finding, and repeat every invalidated profile
   and independent review.
6. Report the processctl phase, cycle, current evidence, blockers, and next owner.
   Never call a task complete from prose alone.

## Hard gates

- Do not edit implementation before specification, required sign-off, and planning.
- Do not review work produced by the same actor or context.
- Do not convert missing, stale, failed, timed-out, or blocked evidence into a pass.
- Do not publish, merge, release, deploy, or perform destructive cleanup unless the
  user separately authorized that project-owned action.

## Output

Return change id, phase, cycle, decisions, current evidence, gaps, blockers, next
owner, and completion record when finished.
