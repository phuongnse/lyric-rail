---
name: change-implement
description: Implement the accepted plan or resolve blocking review findings when routed by deliver-change, without changing the contract implicitly.
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
Follow every applicable **production-engineering** assessment and make its named work
items produce the planned objective evidence. If implementation proves an
applicability decision wrong, preserve the accepted contract and implement the
required structure; the independent review records the corrected semantic result.

Do not weaken an affected enforced readiness capability or silently change its pack,
version, profile mapping, state, or gap. A planned-to-enforced transition must be an
explicit implementation diff backed by the planned checks. Never auto-promote a
capability because implementation appears complete, and do not work unrelated planned
gaps merely because they are listed.

When evidence exposes a contract gap, stop and ask the project owner to supersede the
contract. Do not make review prose into new scope. When implementation is ready,
route to **change-verify**.
