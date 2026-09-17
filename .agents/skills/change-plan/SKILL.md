---
name: change-plan
description: Plan the registered change and its verification boundary when deliver-change routes a specified change.
---

# Plan a change

Read the registered contract and inspect the affected code. Describe one coherent
approach, bounded work items with owned paths, and concrete risks with mitigations.
The plan must bind the exact contract digest and must not add behavior that the
contract did not accept.

Use the existing `approach` to explain material design decisions against the accepted
criteria and inspected consumer code: responsibility and state ownership,
collaboration contracts, reusable structures, and actual sources of variation.
Explain how the chosen structure helps trace the behavior and contains a concrete
maintenance change, accounting for indirection and coupling. Bind needed design work
to existing work-item outcomes. Trace the causal chain from observed symptoms to
underlying structural defects; plan root-cause corrections rather than local
symptom-patching workarounds. Explicitly reject forbidden workarounds: do not
approximate open-world meaning with heuristic token lists, do not introduce
special-case branches for unmanaged callers or tools, do not mask errors or add
silent fallbacks, and do not leak ambient host state into deterministic authority
boundaries. Decouple volatile external state from core decision logic rather than
attempting to filter it. Keep reasoning proportional; retaining a clear existing
structure is a valid choice.

For every material criterion or risk, make the chain reviewable: observed behavior or
failure mode, violated contract or invariant, actual mechanism that can produce it,
smallest sufficient changed boundary, and objective evidence that would distinguish
the faulty state from the correction. `workItems[].affectedPaths` are literal
repository-relative file or directory boundaries for the candidate diff. Declare only
the paths needed by the accepted work; the lifecycle checks that every new candidate
path is inside one of these boundaries before final verification and review. That
machine check proves scope declaration only. It does not prove that the boundary is
minimal, that the mechanism is the true cause, or that an evidence choice is
semantically adequate; those remain independent-review judgments.

Identify project knowledge this change would make misleading, incomplete, or obsolete,
and concrete information gaps obstructing the accepted work. Plan only the necessary
updates or additions in consumer-owned sources. Any proposed cleanup names the
obstacle, smallest useful repair, and expected benefit; unrelated gaps remain
non-blocking proposals.

Explain a material or non-obvious evidence strategy in the existing `approach`, tied
to accepted behavior, contract boundaries, and concrete risks. Derive expectations
for new deterministic behavior from acceptance criteria and important cases; preserve
behavioral evidence for refactors, and select an integration boundary capable of
observing the relevant contract or failure. Documentation, configuration, visual, or
exploratory work may use suitable checks or contextual review. These are defaults;
preserve consumer-mandated testing policies. An obvious existing verification path
needs no repeated rationale or additional record.

When planning a process adoption change, keep the project's baseline
`requiredProfiles` in the contract; do not attempt to omit them. Classify the
adoption boundary in the `approach`:
1. guidance-only/managed-skill update: plan adoption integrity checks (`processctl adoption check`, hash lock, doctor) and publication metadata;
2. process runtime, dependency, or schema contract break: plan adoption integrity, direct rejection of superseded inputs, consumer recovery, and verification of affected runtime boundaries;
3. mixed adoption with consumer product source or policy edits: plan full normal consumer-required verification profiles;
4. incomplete or unknown impact: plan the complete baseline verification path without waiver.
Where prior passing profile evidence is valid and consumer source is unchanged, plan
continuation verification via `processctl change verify --remaining`.

When a consumer adopts the impact-selection capability, plan its current version-1
`impactProfiles` policy as consumer-owned evidence. Map every candidate path to one
or more independently executable units, use a global unit only when its declared
paths include the explicit universal `**` pattern and dependency reach is
intentionally cross-cutting, and define the agent action for an
    unresolved path. An optional `finalProfiles` declaration in the same current policy
    must name only required profiles and include an explicit global unit for each; the
    consumer owns the claim that the selected units are independent and complete. Do
    not infer final coverage from filenames or commands. Unresolved feedback or final
    assurance impact must block the corresponding path; an explicit full-profile
    refresh remains available as a deliberate command, never as a fallback. Consumers
    without the declaration keep the normal required profiles as their final assurance
    boundary.

Read **production-engineering** and add one `productionEngineering` assessment for
each canonical invariant in its defined order. Decide applicability from the stated
trigger, give a concrete rationale, and bind every applicable invariant to the work
items that will implement its required structure and evidence. A not-applicable
assessment has no evidence work items and cannot waive a known violation.

For each affected readiness capability, make the protecting or advancing work visible
in an existing work-item outcome and verification boundary. Preserve every enforced
capability; do not add unrelated planned gaps as hardening scope. If the contract aims
to promote a planned capability, plan the consumer-owned readiness diff and the exact
evidence that would justify `enforced`; a checklist edit alone is not evidence.

Validate and register it:

    processctl contract validate --kind plan plan.json
    processctl change plan --change-id ID --actor ACTOR --context CONTEXT --plan plan.json

Escalate genuinely missing product or architecture decisions to the project owner.
There is no synthetic pre-implementation review gate; independent review happens on
the implemented, verified snapshot.
