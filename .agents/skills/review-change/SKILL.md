---
name: review-change
description: Independently review an immutable verified change against its contract, plan, project policy, diff, and current evidence, then produce structured findings and an approval or changes-requested verdict. Use only from a reviewer actor and isolated context that did not implement the reviewed cycle.
---

# Review a Change

## Goal

Evaluate correctness, security, compatibility, maintainability, and evidence at a
specific checkpoint without changing the reviewed source.

## Workflow

1. Accept the review only in a reviewer actor and a fresh isolated context that did
   not implement the current cycle and did not perform an earlier review assignment
   in the project. The context must not inherit implementation or prior-review
   conversation. Use the host's isolated-review mechanism or a separate human
   reviewer; if separation cannot be attested, report blocked. A stable reviewer
   actor or role may be reused, but renaming a retained context is not isolation.
   The consumer host chooses the agent, model, person, and spawn mechanism; portable
   process owns only the isolation, attestation, assignment, report, and evidence contract.
2. Register the assignment with processctl change review start. Confirm its
   checkpoint, comparison base, contract, plan, and required verification reports
   refer to the same immutable source.
   For an authority transition, use the candidate root named by the coordinator and
   require review schema 4 to carry the exact registered request and candidate
   evidence references. Review the candidate diff while treating target-generated
   claims as untrusted; the N-1 control lifecycle remains the assignment owner.
3. Read the diff and only the project-owned contracts needed to evaluate affected
   behavior and trust boundaries. Reconcile every `production-v1` assessment,
   including security, privacy, reliability, performance, observability, operability,
   compatibility, maintainability, correctness, and supply-chain integrity.
4. Record actionable findings with severity, exact location, evidence, and status.
   Record one structured quality assessment per accepted contract dimension; a
   requested change marks at least one applicable dimension failed, while approval
   requires every applicable dimension verified and every N/A rationale confirmed.
   Separate defects from questions, optional improvements, and unsupported claims.
   Resolved, deferred, and false-positive findings require resolution evidence.
   Deferred remains unresolved and completion-blocking unless a future process
   contract introduces an explicit owner-approved exception.
   Treat owner mismatch, consumer workaround for a shared defect, evidence-free rerun,
   missing valid/fail-closed regression proof, or missing affected-consumer proof for
   a shared correction as required completion-blocking findings.
   Also treat an implementation that silently selected an unresolved project-owner
   decision about scope, authority, trust boundary, compatibility, rollout, or
   lifecycle order as completion-blocking, even when its tests pass.
   Confirm every observed failure and carried finding has the correct improvement
   owner, reusable class, invariant id, disposition, recurrence state, and required
   local or federated proof. A repeated resolved invariant closed as another narrow
   incident, or a shared case without signal-chain ownership, is required.
   For a finite-boundary change, classify every new finding exactly once as
   `covered` or `contract-gap`. A covered finding binds one declared fault row, its
   derived trust boundary, and the row's complete criterion set. A contract gap binds
   no row, stays open, and immediately requires owner decision and a superseding
   contract; review must not extend the contract in prose. Carry these binding fields
   immutably in later cycles. Review only the accepted diff, carried findings and
   declared affected trust boundaries; do not continue instruction-by-instruction
   fault invention outside the closed rows.
5. Request changes when any required finding remains open or deferred. Approve only
   when required outcomes and evidence are complete for the reviewed checkpoint.
6. Validate the report with processctl contract validate --kind review, then submit
   it with processctl change review submit. A coordinator may transport the assigned
   reviewer's exact artifact; the host or human attester owns its authenticity. The
   CLI rejects implementation identity reuse at assignment, stale evidence, missing
   carried findings, assignment mismatches, and checkpoint mismatches.
7. Return findings to the implementation owner. Review never silently edits,
   publishes, or expands the accepted scope.
   Lifecycle core counts each distinct changes-requested review cycle independently
   of finding names. The third cycle in one decision window stops another autonomous
   implementation cycle and requires the existing reviewed recommendation plus owner
   resolution. This stop never approves or resolves findings.

## Hard gates

- Do not approve stale, indirect, missing, or blocked evidence.
- Do not treat review prose as more authoritative than project contracts.
- A reviewer must not intentionally mutate the checkpoint under review.
- Do not approve an unclassified improvement case or an artifact chain whose next
  owner contradicts the owning contract.
- Static lint, policy, secret, pin, configuration, or CI checks are supplemental
  verification and cannot issue a semantic review verdict.
- The reviewer actor id and context id must both be independent from every
  implementation actor and context recorded for the current cycle.
- The reviewer context id must be unique across review assignments for the project,
  and the attester must bind it to a genuinely fresh context rather than a renamed
  retained conversation.

## Output

Return the structured report, attested reviewer identity, verdict, reviewed
checkpoint and base, unresolved findings, lifecycle phase, and invalidated evidence.
