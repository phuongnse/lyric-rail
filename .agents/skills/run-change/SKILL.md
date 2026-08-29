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
   - improvement-required: use evolve-process and classify before corrective work;
   - improvement-pending: use cross-repo-change and wait for the required artifact chain;
   - verified: obtain a separate reviewer through review-change;
   - review-pending: wait for the assigned reviewer; do not self-review;
   - approved: use finish-change;
   - completed: use publish-change when standing project policy authorizes automatic
     publication and continue through exact-head merge, release, deployment, adoption,
     and cleanup gates until the authorized operation is terminal;
3. Apply the nearest AGENTS.md and domain skill inside each phase. Project policy may
   add stronger gates but cannot remove lifecycle phases, baseline profiles,
   independent review, evidence freshness, or finding closure.
4. When a command, gate, release, adoption, or external integration fails, apply the
   failure-to-invariant protocol in the required execution reference before any
   corrective mutation. The lifecycle enters `improvement-required`; keep dependent
   candidates blocked until owner, reusable class, invariant, disposition, and local
   or federated proof are explicit.
5. When new evidence exposes a material owner decision or multiple valid boundary
   choices, apply the owner-decision escalation protocol in the execution reference;
   do not choose a new scope, authority, architecture, or lifecycle route implicitly.
6. After a valid finding, preserve the reviewed checkpoint, begin the next
   implementation cycle, resolve the finding, and repeat every invalidated profile
   and independent review.
7. Report the processctl phase, cycle, current evidence, blockers, and next owner.
   Never call a task complete from prose alone.

## Hard gates

- Do not edit implementation before specification, required sign-off, and planning.
- Do not review work produced by the same actor or context.
- Do not convert missing, stale, failed, timed-out, or blocked evidence into a pass.
- Do not continue verification, completion, or publication through
  `improvement-required` or `improvement-pending`.
- Do not publish before completion. Standing project policy may authorize automatic
  commit, push, branch/PR creation, and exact-head merge after completion.
- Treat a valid standing policy as authorization for its declared release,
  deployment, adoption, and ephemeral-cleanup operations;
  do not request redundant per-action confirmation.
- Escalate only when a required capability or authority is unavailable, bounded
  recovery is exhausted, or a material product/security decision is missing.

## Output

Return change id, phase, cycle, decisions, current evidence, gaps, blockers, next
owner, and completion record when finished.
