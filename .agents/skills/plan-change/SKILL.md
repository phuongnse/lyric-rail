---
name: plan-change
description: Produce the smallest implementation plan that covers every accepted outcome and its verification boundary.
---

# Plan a change

Read the registered contract and inspect the affected code. Describe one coherent
approach, bounded work items with owned paths, and concrete risks with mitigations.
The plan must bind the exact contract digest and must not add behavior that the
contract did not accept.

Validate and register it:

    processctl contract validate --kind plan plan.json
    processctl change plan --change-id ID --actor ACTOR --context CONTEXT --plan plan.json

Escalate genuinely missing product or architecture decisions to the project owner.
There is no synthetic pre-implementation review gate; independent review happens on
the implemented, verified snapshot.
