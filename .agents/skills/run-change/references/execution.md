# Portable Execution Contract

Apply these semantics throughout every change lifecycle.

## Universal gates

1. Read the full active skill, this reference, the nearest AGENTS.md, and the affected
   project owners before editing.
2. Follow phase order. A stated stop condition blocks dependent work.
3. Defer a specific accepted outcome only with explicit approval and an owner. A skip
   never waives dependent gates implicitly.
4. Reuse evidence only while its artifact, checkpoint, workspace fingerprint,
   command, environment, and acceptance boundary remain current.
5. Remove superseded implementation and guidance when compatibility is not required.
   Do not preserve a retired path as an undocumented safety default.
6. Separate development completion from commit creation, publication, merge, release,
   deployment, and destructive data operations.

## Standing gated automation

A valid project-owned standing automation policy is continuing authorization for its
declared routine operations. An owner directive may authorize a completed change that
installs the policy but never substitutes for missing or invalid policy. After each
owning gate passes, continue commit, push, review-object,
exact-head merge, release, publication, deployment, adoption, and ephemeral cleanup
without requesting per-action confirmation. Policy never waives lifecycle order,
independent review, current evidence, exact head/base, required checks, branch
protection, release identity, consumer ownership, or destructive-target validation.

Escalation is exceptions-only. Involve the owner only when a required action or
authority is unavailable (`capability-unavailable`), bounded idempotent recovery is
exhausted (`bounded-recovery-exhausted`), or a material product/security choice is
missing (`decision-required`). Pending checks, ordinary retries, hard work, and a
routine authorized merge are not escalation reasons.

## Change-driven scope

Map affected paths, callers, consumers, trust boundaries, migrations, generated
artifacts, documentation, and evidence-required dependencies to complete work items.
Run the smallest profile that proves each accepted outcome. Use a broader profile only
when cross-cutting invalidation, inseparable dependencies, or project policy requires
it. Do not infer broad completion from a focused check.

## Engineering method

1. Trace the governing contract and real flow before choosing an owner or design.
2. Prefer no change, existing code, the standard library, native platform behavior,
   and installed dependencies before custom mechanisms, while preserving required
   safety and acceptance behavior.
3. For a defect, prove the smallest reliable failure first, state one hypothesis, test
   one variable, implement the root-cause fix, and prove the behavior afterward.
4. Treat a proposed path as a workaround when it changes the required owner, runtime,
   authority, trust boundary, invariant, or evidence boundary merely to keep moving.
   Return to specification and planning instead.
5. Keep one writer for overlapping source. Delegate bounded disjoint work only when
   the host supports it and the handoff preserves exact scope, permissions, stop
   conditions, and evidence ownership.

## Blocker protocol

When progress depends on user-controlled or external state and no standing policy
already authorizes the required operation:

1. Classify repository defect, missing product decision, or external-state blocker.
2. Reproduce through the smallest permitted boundary and preserve the exact command,
   exit status, error, environment, and missing authority.
3. Continue safe read-only diagnosis, but stop mutation at authentication, consent,
   permission, host setup, destructive action, or approval boundaries.
4. Do not substitute a different command, library, runtime, environment, proxy,
   credential path, disabled control, or indirect API as evidence for the required
   boundary.
5. Report `Blocker`, `Evidence`, `Boundary`, `User action or decision needed`, and
   `Safe next step after confirmation`.

## Owner decision and escalation

Do not convert ambiguity into autonomous architecture or process experimentation.
When new evidence creates more than one materially valid direction, or a choice would
change accepted scope, owner, trust boundary, authority, compatibility, rollout,
lifecycle order, or external mutation, stop dependent mutation and:

1. Separate a discoverable implementation detail already inside accepted scope from
   a project-owner decision. Continue autonomously only for the former.
2. Preserve the evidence and state the invariant that every valid option must keep.
3. Present the genuinely valid options, their trade-offs, and one evidence-backed
   recommendation. Do not manufacture a weak option to make the recommendation look
   inevitable.
4. Request an explicit owner decision and record it in the owning contract, plan, or
   durable project decision before continuing dependent work.
5. If a failed attempt disproves an assumption and the next direction changes one of
   these boundaries, return to this protocol instead of trying another architecture,
   trust path, command, or workflow loop autonomously.

This protocol does not require owner confirmation for bounded implementation choices
whose behavior and authority are already decided. A question or status request never
weakens existing safe read-only diagnosis.

## Failure-to-invariant protocol

Apply this protocol before corrective mutation whenever a command, gate, release,
adoption, or external integration produces a validated failure:

1. Preserve the smallest reliable reproducer, exact command/event, immutable source
   identity, environment, exit status, bounded output, and available service evidence.
   Do not use an evidence-free rerun as diagnosis. A process-owned command that exits
   zero while emitting a classified warning or error is a failure with the same
   preservation requirement; exit status never suppresses diagnostic evidence.
2. Classify the owning boundary as `project-local`, `shared-process`,
   `operations-or-external`, or `missing-product-or-authorization-input`. State the
   evidence that excludes the other boundaries before selecting a fix.
3. Keep every dependent candidate blocked. A shared-process defect must be fixed in
   the shared producer; do not add a consumer-owned wrapper, duplicate algorithm,
   alternate authority, relaxed control, or environment substitution to keep moving.
   Do not silence a warning/error diagnostic or replace the canonical command merely
   to make evidence pass. A project-local behavior remains in the project owner and
   must not be promoted to portable core without evidence of a reusable class.
4. Add regression evidence at the lowest reliable owner boundary for both the valid
   behavior and the corresponding persistent, invalid, timeout, or interruption case
   that must remain fail closed.
5. For a shared correction, require producer profiles and a reproduction at every
   affected consumer boundary before release authorization. Consumer proof supplements
   producer evidence; neither substitutes for the other.
6. Treat operations or external propagation as transient only when source and
   configuration are already proven unchanged. Recovery must be bounded, idempotent,
   preserve per-attempt diagnostics, and stop on a deterministic failure. Do not
   change source, branch, version, credentials, or controls merely to cause another
   attempt.
7. Reopen or create the owning change lifecycle when scope moves across a boundary,
   then repeat invalidated verification and independent review on one exact final
   checkpoint. Record the reusable invariant, not just the incident chronology.

### Federated improvement handoff

A governed verification failure or unresolved review finding transitions to
`improvement-required`, owned by evolve-process. Classify it before corrective work.
A reviewed project-local case returns to implementation. A shared consumer case
transitions to `improvement-pending`, exports a bounded untrusted signal, and is owned
by cross-repo-change until the producer disposition, completed lifecycle and immutable
release resolution, and exact consumer reproduction all validate.

Signal, disposition, producer completion, pre-release candidate, resolution, and
reproduction are distinct authority boundaries. Portable core reads and writes local
artifacts only. Transports preserve exact bytes and cannot mutate either repository.

## Independent review

Review begins only after all baseline and change-required profiles pass on one clean
immutable checkpoint. The reviewer must be a read-only actor and a fresh context
unused by the current implementation cycle or an earlier review assignment in the
project. The context must not inherit implementation or prior-review conversation; a
new label on retained context is not fresh isolation. The agent host or human
organization attests that identity separation; processctl validates the attestation
structure and rejects implementation identity reuse, reviewer-context reuse, or stale
evidence. A stable reviewer actor or role remains portable and may be reused with a
genuinely fresh context.

A running or pending reviewer means review pending, not failure or approval. The
reviewer reads the assignment, diff, contracts, plan, and existing evidence; it runs
only a focused reproducer for a concrete finding or evidence gap. It never edits
tracked source or Git state. Any source mutation invalidates the assignment.

An open required finding produces changes-requested. Preserve its checkpoint and
evidence, classify the finding against the owning contract, implement the smallest
correct resolution in a new cycle, and repeat invalidated verification and review.

## Completion audit

Map every acceptance criterion to current source and required verification. Require
an approved independent review for the exact same checkpoint and workspace
fingerprint, with no open required finding. Missing, stale, indirect, or blocked
evidence remains incomplete. processctl completion is an engineering result, not
publication or release authorization.

## Publication and merge chain

The canonical chain is implementation and focused correction, every required profile,
independent semantic agent or human review of the clean checkpoint, finding resolution
with complete re-verification and fresh review, completion, then review-object
publication from that exact checkpoint. Static policy checks supplement but never
replace semantic review. By default, no branch or pull/merge review object is created
earlier.

A project may explicitly opt into a controlled automation-proposal contract on its
protected base. This is a narrow exception for an untrusted dependency proposal, not
source publication or a lifecycle transition. Before exposing the proposal, the
project-owned adapter must produce bounded policy evidence for the exact base, head,
changed paths, automation owner, title/body, immutable verifier, and required controls,
resolve the actual protected-base commit independently from the provider event, then
pass both to `publication validate-proposal`. The controls disable automerge, scripts,
plugins, shell execution, privileged or write-capable proposal checks, and exclude
process-authority, workflow, release, deployment, security-policy, and trust-root
changes. Missing or disabled base policy fails closed. Provider draft/ready state is
presentation only and grants no authority.

Branch protection keeps the configured `lifecycle-completion` check absent for a new
proposal head. After the exact proposal source completes every required profile,
independent review, finding loop, and `change finish`, the adapter must pass
`publication validate-proposal-completion` against fresh policy evidence for the same
base/head, finalized ready metadata, clean source, and external completion receipt.
Only then may the provider adapter create that exact-head check. A changed head has no
inherited authorization;
the protected branch must require the proposal to be current with its exact validated
base before merge; duplicate mismatch fails closed. Schema-1 proposal policy preserves
its historical human-only meaning. Schema 2 keeps provider automerge disabled before
completion, then permits exact-head merge only through the protected-base standing
policy. Existing consumers remain on schema 1 until a separately completed opt-in
change is merged.

After completion, standing consumer policy may automate branch push and review-object
creation, required-check waiting, exact-head merge, release, deployment, adoption, and
ephemeral cleanup. Provider draft/ready state is non-normative. Automation stops only
at a terminal result or one of the three declared escalation reasons.

## Authority rotation

When a change replaces a verifier, signing root, release controller, process
authority, or other self-hosted trust root, split introduction from cutover whenever
the new immutable identity cannot exist before publication:

1. The currently trusted authority governs specification, verification, semantic
   review, completion, and publication of the new authority.
2. Resolve the new authority from an immutable identity that is publicly available to
   its consumer; never pin a mutable label or an unmerged/unpublished commit.
3. Prove the new authority on the exact cutover checkpoint before changing live
   policy or removing the old authority. Preserve an old-or-new recovery route without
   creating a gap where neither control applies.
4. Retire the old authority only after the new identity, caller, and enforcement
   boundary are active and independently reviewed.

Each separately published stage is its own lifecycle change. Provider-specific
default-branch, check-context, key-store, or artifact mechanics belong to the project
adapter and must not become portable lifecycle phases.

## Process improvement

Classify a validated defect or finding as local behavior, reusable process semantics,
deterministic enforcement, portability gap, or obsolete guidance. Fix the smallest
correct owner, add regression proof for deterministic behavior, and remove duplicate
or superseded rules. Do not memorialize an incident as ceremony without evidence of
the reusable class. Assign producer-canonical invariant ids and consult the versioned
catalog: a signal for an already resolved invariant is a recurrence and cannot close
as another non-shared narrow fix without explicit owner-approved exception evidence.
