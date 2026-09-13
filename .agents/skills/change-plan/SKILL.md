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
to existing work-item outcomes. Keep reasoning proportional; retaining a clear
existing structure is a valid choice.

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
