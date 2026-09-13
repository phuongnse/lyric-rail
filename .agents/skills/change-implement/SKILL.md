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

Implement only the current plan and keep consumer-owned policy in the consumer.
For a deterministic defect, add focused regression evidence that distinguishes the
reported faulty behavior from the correction. When a known-faulty state is safely
available, demonstrate that distinction; no proof of test-authoring or execution
order is required. Each added or materially changed test or check must protect accepted
behavior, a contract boundary, or a concrete risk, rather than merely execute changed
code or mirror its structure. Retain consumer-mandated checks; do not manufacture a
new automated test when existing evidence suffices or other evidence better fits the
change. Focused checks support diagnosis and implementation; required full profiles
still run on the final unchanged candidate.

Update, supplement, or create only the project information needed for the planned
knowledge repairs, using inspected behavior, accepted requirements, and recorded
decisions. Replaced material is updated, removed, or visibly marked historical or
superseded when retaining it is useful. Explain enough for the intended reader and
accepted work, in a logical order, with references that clarify relevant relationships.

If review requested changes,
resolve every blocking finding in the next cycle without renaming or dropping it.
Follow every applicable **production-engineering** assessment and make its named work
items produce the planned objective evidence. If implementation proves an
applicability decision wrong, preserve the accepted contract and implement the
required structure; the independent review records the corrected semantic result.

Complete the accepted design work before final verification. Re-read the affected
end-to-end flow and its callers using **production-engineering** design guidance;
refactor within scope when the implemented structure obscures responsibility,
weakens contracts, or adds unnecessary coupling or indirection. Preserve behavior
and protective checks during refactoring. Working code and passing tests do not
replace meeting accepted design criteria.

Do not weaken an affected enforced readiness capability or silently change its pack,
version, profile mapping, state, or gap. A planned-to-enforced transition must be an
explicit implementation diff backed by the planned checks. Never auto-promote a
capability because implementation appears complete, and do not work unrelated planned
gaps merely because they are listed.

When evidence exposes a contract gap, stop and ask the project owner to supersede the
contract. Do not make review prose into new scope. When implementation is ready,
route to **change-verify**.
