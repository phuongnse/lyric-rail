---
name: plan-change
description: Produce the smallest implementation plan that covers every accepted outcome and its verification boundary.
---

# Plan a change

Read the registered contract and inspect the affected code. Describe one coherent
approach, bounded work items with owned paths, and concrete risks with mitigations.
The plan must bind the exact contract digest and must not add behavior that the
contract did not accept.

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
