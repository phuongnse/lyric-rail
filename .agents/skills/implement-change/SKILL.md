---
name: implement-change
description: Implement a registered engineering plan, preserve scope and project policy, and prepare a clean immutable checkpoint for required verification. Use after processctl reports the change as planned or after independent review requests changes.
---

# Implement a Change

## Goal

Produce the smallest complete implementation of the registered plan and return it to
the canonical verification and review lifecycle.

## Workflow

1. Confirm processctl change status reports planned or changes-requested, then register
   the implementation actor and isolated context with processctl change implement.
2. Read the contract, plan, nearest AGENTS.md, and affected project owners. Resolve a
   conflict through the owning contract instead of silently changing scope.
3. Implement work items in dependency order. Add or update proving tests at the lowest
   reliable boundary and keep generated artifacts, migrations, callers, and docs in
   the same accepted slice.
   Preserve declared resource bounds, observability, cleanup, trust boundaries, and
   release identity as implementation invariants rather than end-of-cycle additions.
4. Run focused project checks while editing. These checks diagnose implementation but
   do not replace lifecycle verification.
5. Reconcile every acceptance criterion and planned work item. Record exact blockers
   or accepted deferrals; never convert missing evidence into completion.
6. Create a clean immutable checkpoint according to project policy, then hand the
   change to verify-change. After review findings, preserve the reviewed checkpoint,
   begin the next implementation cycle, resolve findings, and repeat verification.

## Hard gates

- Do not implement before a valid plan and required sign-off.
- Do not expand product behavior, compatibility, dependencies, or trust boundaries
  without returning to specification and planning.
- The implementation actor cannot perform or approve independent review for a cycle
  in which it implemented source.

## Output

Return change id, cycle, implemented work items, changed owners, focused evidence,
checkpoint readiness, gaps, and next owner.
