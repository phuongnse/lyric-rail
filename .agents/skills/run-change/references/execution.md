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
2. Preserve the evidence and enumerate every governing hard invariant and material
   assumption before ranking any option.
3. Create a bounded recommendation artifact. Assess every option against every
   invariant, bind proven assumptions to evidence, and derive the complete `valid`,
   `invalid`, and `unproven` sets. Cost, convenience, minimal change, and other
   secondary optimization apply only within the derived valid set. Never promote an
   invalid or unproven option because it changes fewer boundaries.
4. For every material recommendation, run `processctl recommendation review start`
   before review. It creates a digest-bound assignment and atomically reserves a
   project-global context unused by lifecycle or recommendation review. The assigned
   independent adversarial reviewer uses a distinct actor and fresh context to
   challenge assumption evidence, invariant tracing, option classification, and
   terminal ordering. Run `processctl
   recommendation validate-chain` with the exact assignment; do not present a
   recommendation unless the complete chain is approved and allowed.
5. If no valid option exists, report `decision-required` with the missing evidence or
   authority instead of manufacturing a recommendation. Otherwise present the valid
   options, their trade-offs, and the reviewed recommendation without inventing a
   weak alternative to make it look inevitable.
6. Request an explicit owner decision and create a digest-bound recommendation
   resolution selecting only a valid option. The resolution records the choice but
   grants no lifecycle completion, merge, release, deployment, or adoption authority.
7. If a failed attempt disproves an assumption and the next direction changes one of
   these boundaries, return to this protocol instead of trying another architecture,
   trust path, command, or workflow loop autonomously.

This protocol does not require owner confirmation for bounded implementation choices
whose behavior and authority are already decided. A question or status request never
weakens existing safe read-only diagnosis.

## Plan decision gate

When the project adopts `provenance-gated-authored-review`, every nontrivial authored
plan uses schema 3 provenance and receives a fresh semantic assessment before each
new implementation cycle. `change decision start` atomically reserves a
project-global context unused by lifecycle or recommendation review. The assigned
read-only reviewer is independent of the plan author and assesses architecture,
authority, compatibility, external mutation, lifecycle order, owner, rollout, scope,
and trust boundary for the exact contract, plan, source checkpoint, authority,
policy, and context. `clear` permits `change implement`. `decision-required` remains
blocked until the existing independently challenged recommendation and explicit
owner resolution bind the exact assessment.

A process-generated bypass is valid only when core recognizes the registered
generator and exactly recomputes the complete plan from bounded validated
source-owned inputs under the immutable authority identity. Unknown generators,
claimed labels, risk tiers, author self-classification, heuristic prose scanning,
and partial comparison fail closed. Exact generated plans do not receive universal
semantic review. Do not add a daemon, scheduler, service, webhook receiver, hosted
reviewer platform, vendor, model, or proprietary agent API to portable core. The host
remains a coordinator or implementation actor, a genuinely fresh read-only reviewer,
and the existing finding and owner-decision loops. Unreviewed prose is candidate-only
and cannot create decision or lifecycle authority.

These are separate artifact boundaries, not a meta-review hierarchy. The plan
reviewer assesses the authored plan. If a decision is required, the recommendation
reviewer challenges the recommendation's invariants, options, and ordering and checks
only that its scope binds the assessment digest; it does not reassess the assessment
or review the plan reviewer. The final change reviewer reviews implemented source and
current evidence, not either prior reviewer. Never add reviewer-of-reviewer,
meta-assessment, assessment-of-assessment, policy-for-policy, a dynamically generated
approval chain, or a generic workflow engine.

After a finding or clean post-verification source drift, the previous assessment is
source-stale. Before `change implement` opens the next cycle, create a new assignment
with a fresh context and submit a new assessment for the new source checkpoint. Do
not relabel or reuse the earlier assignment, reviewer context, or owner-decision
artifacts.

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

## Exact remote verification

Projects own named remote-evidence requirements in their manifest: local profiles,
provider execution identity, and bounded platform/runtime selectors. A schema-3
change selects requirement ids without embedding transport mechanics. On a clean
immutable implementation checkpoint, `change remote request` binds the change,
cycle, source, comparison base, workspace fingerprint, requirement expansion, and
base-owned workflow checkpoint while granting no review or delivery authority.

The provider adapter transports only that exact checkpoint through a non-review
verification object, obtains bounded supplemental bundles, preserves service ids and
digests, and calls `change remote ingest` locally. Core performs no network work and
trusts neither a job conclusion nor adapter prose: it verifies one-to-one selector
coverage, archive bytes, service digest, manifest/report hashes, execution identity,
clean diagnostics, bounds, and exact source identity. Missing, stale, failed,
duplicate, truncated, redirected, or mismatched evidence keeps the lifecycle
implementing. The adapter removes its ephemeral verification object on success,
failure, timeout, and interrupt after preserving evidence. Review cannot begin until
the complete required local and remote set passes.

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

### Finite correction windows

Under the adopted bounded-loop authority, every newly registered lifecycle starts a
prospective decision window with a hard limit of three distinct final-review cycles
whose verdict is changes-requested. Core counts cycles, not finding ids, invariant
labels, locations, severities, splits, or carried-resolution state. The third review
keeps every finding and improvement case blocking, records a digest-bound escalation,
and refuses a fourth implementation cycle.

Recovery reuses the existing plan-decision and recommendation authority. The fresh
plan assignment binds the escalation; a clear assessment is invalid. A
decision-required assessment, independent recommendation challenge, and explicit
owner resolution may authorize one fresh three-cycle correction window for findings
inside the accepted boundary, or may supersede the change. The complete resolved
chain remains separate from the mutable current plan-decision slot and is retained in
completion and receipt evidence. No reviewer-of-reviewer or loop-arbitration phase is
created.

A schema-4 finite review boundary is a closed set of trust boundaries and fault rows.
Each row declares its trigger or injection boundary, expected outcome, mapped
acceptance criteria, proving profiles/evidence, and stop condition. Every new finding
is exactly `covered` or `contract-gap`. Covered findings bind one row and its derived
trust/criterion identity. A contract gap binds no row, stays unresolved, escalates on
the first review, and permanently stops the current lifecycle after owner decision;
recovery requires a separately specified superseding change. Historical lifecycle
artifacts retain their released meaning and receive no retroactive decision window.

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
protected base. Schemas 1 and 2 preserve the narrow untrusted dependency-proposal
route. Schema 3 separately governs a Renovate `process-adoption` proposal; neither is
source publication or a lifecycle transition. Before exposing a dependency proposal, the
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

For schema-3 process adoption, the protected-base policy fixes Renovate, the producer
repository, the immutable verifier repository/commit, `consumer-owner-merge`, automerge false,
`consumerOwnerMergeRequired` true, and post-merge mutation false. The verifier binds
the actual base independently plus the exact producer release/tag/commit/attestation,
source and target authority identities, requirements bytes, process lock, complete
selected managed distribution, declared migration, bounded raw release/attestation
bytes and artifact hashes, every producer use in the unchanged-mode regular-workflow
tree, head/path set, and metadata. Report bytes do not authenticate themselves: supply
a separate clean tagged producer checkout, release artifacts, receipt, and attestation
to the existing release validators, then compare exact target synchronization. Base
must be an ancestor of head. Candidate-owned commands run only after
that validation. The consumer then owns project checks, review choice, and the manual
merge decision. Merge is terminal; do not create lifecycle-completion for this route,
apply standing auto-merge, or schedule synchronization after merge.

Keep origin routes distinct. An agent-host candidate published only after exact
lifecycle completion remains eligible for standing-policy auto-merge. A Renovate
process-adoption proposal is created before consumer-owner review and can never inherit
that authority, even if later checks or lifecycle work pass. Reuse managed adoption,
ordinary consumer CI/review, and branch protection; do not build a reviewer host,
daemon, scheduler, dynamic approval chain, meta-assessment, or generic workflow engine.

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

When the live process lock itself is the candidate, use an explicit two-workspace
authority transition. N-1 keeps a clean control workspace and registers source/target
identity, base, paths, migration, assets and expiry before mutation. N+1 may change
only a separate candidate and emit bounded evidence. N-1 must independently recreate
the candidate through observed target apply/check, a second idempotent apply/check,
and an after-write rollback probe in disposable exact-base worktrees before it ingests that evidence and
runs verification, remote evidence, review, completion and publication against the
exact candidate root. A transition-only lifecycle never becomes a generic lock bypass.

If immutable N-1 predates this route, a one-time bootstrap is valid only when N-1
completes and exports an exact intent/policy checkpoint, a verifier fixed to the
already N-1-governed protected-base source interprets it outside target/candidate
checkouts, and a typed project-owned policy fixes the workflow, check, current base,
head, target and protected merge. Successful merge is atomic single-use consumption
and the sole activation event. Terminal consumption authenticates the validation
artifact's repository, workflow SHA/path, run, exact App-owned completion check,
base/head/merge tree and records a durable exclusive provider identity.

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
