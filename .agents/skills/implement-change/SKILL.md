---
name: implement-change
description: Implement the accepted plan or resolve blocking review findings without changing the contract implicitly.
---

# Implement a change

Register the implementation identity before editing:

    processctl change implement --change-id ID --actor ACTOR --context CONTEXT

Every delegated actor/context that may mutate the candidate must run the same command
before its first edit. During an active cycle the command appends that participant
without starting another cycle; review checks all registered participants.

Implement only the current plan. Add focused regression coverage for deterministic
defects and keep consumer-owned policy in the consumer. If review requested changes,
resolve every blocking finding in the next cycle without renaming or dropping it.

When evidence exposes a contract gap, stop and ask the project owner to supersede the
contract. Do not make review prose into new scope. When implementation is ready,
route to **verify-change**.
