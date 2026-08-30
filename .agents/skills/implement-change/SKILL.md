---
name: implement-change
description: Implement a registered engineering plan, preserve scope and project policy, and prepare a clean immutable checkpoint for required verification. Use after processctl reports the change as planned or after independent review requests changes.
---

# Implement a Change

## Goal

Produce the smallest complete implementation of the registered plan and return it to
the canonical verification and review lifecycle.

## Workflow

1. Confirm processctl change status reports planned or changes-requested. When the
   project has adopted plan-decision enforcement, require an exactly recomputed
   registered generated plan or current authored-plan evidence from a genuinely
   fresh read-only reviewer. A clear assessment may proceed; `decision-required`
   needs the exact reviewed recommendation and owner resolution. A finding or clean
   post-verification source drift invalidates the prior assessment, so refresh it in
   a new context before registering the next cycle. Then register the implementation
   actor and isolated context with processctl change implement.
   If status reports a review-loop escalation, stop. A fourth cycle is authorized
   only by its escalation-bound decision-required assessment, independently reviewed
   recommendation, and explicit owner resolution. A superseded contract-gap
   lifecycle never resumes.
2. Read the contract, plan, nearest AGENTS.md, and affected project owners. Resolve a
   conflict through the owning contract instead of silently changing scope.
3. Implement work items in dependency order. Add or update proving tests at the lowest
   reliable boundary and keep generated artifacts, migrations, callers, and docs in
   the same accepted slice.
   Before implementing a standard parser, protocol, serializer, cryptographic
   primitive, or platform adapter, evaluate maintained dependencies and supported
   project tools. Prefer a suitable dependency through the managed lock flow; write a
   custom implementation only with evidence that available dependencies cannot meet
   the accepted compatibility, licensing, supply-chain, portability, or resource
   contract.
   Preserve declared resource bounds, observability, cleanup, trust boundaries, and
   release identity as implementation invariants rather than end-of-cycle additions.
   For an authority transition, keep the N-1 control workspace clean and register the
   exact transition before changing a separate candidate workspace. N+1 may
   materialize the candidate and emit `authority-transition candidate-evidence`; it
   must expose observed apply/check/idempotence and after-write rollback behavior in
   disposable worktrees whose trees N-1 independently recomputes. It
   must not run lifecycle verification, review, completion, publication, or merge.
4. Run focused project checks while editing. These checks diagnose implementation but
   do not replace lifecycle verification.
5. Reconcile every acceptance criterion and planned work item. Record exact blockers
   or accepted deferrals; never convert missing evidence into completion.
6. Create a clean immutable checkpoint according to project policy, then hand the
   change to verify-change. After review findings, preserve the reviewed checkpoint,
   begin the next implementation cycle, resolve findings, and repeat verification.

## Hard gates

- Do not implement before a valid plan and required sign-off.
- Do not implement through missing, stale, scope-mismatched, self-authored, or
  unresolved plan-decision evidence.
- Do not expand product behavior, compatibility, dependencies, or trust boundaries
  without returning to specification and planning.
- Do not recreate a mature standard or protocol merely to avoid proposing or
  installing a useful managed dependency.
- The implementation actor cannot perform or approve independent review for a cycle
  in which it implemented source.

## Output

Return change id, cycle, implemented work items, changed owners, focused evidence,
checkpoint readiness, gaps, and next owner.
