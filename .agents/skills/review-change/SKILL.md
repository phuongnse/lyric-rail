---
name: review-change
description: Review the exact verified snapshot from an actor and context independent of its implementation.
---

# Review a change

The reviewer must not share either actor identity or execution context with an
implementer in the current cycle. Start the assignment:

    processctl change review start --change-id ID --actor REVIEWER --context REVIEW_CONTEXT

Review the accepted contract, plan, complete diff, focused tests, and verification
evidence. The first pass is comprehensive within that frozen contract. Every finding
maps to one accepted criterion and records priority, origin, severity, and location.
Ideas outside the contract are proposals, not blocking findings. approved may contain
non-blocking observations but no blocking finding; changes-requested requires at least
one blocking finding. Write the report to the reportPath returned by review
start; that path is process state and does not mutate the reviewed snapshot.

Validate and submit the report:

    processctl contract validate --kind review REPORT_PATH
    processctl change review submit --change-id ID --review REPORT_PATH

Review is read-only. Requested changes route back to **implement-change**; approval
routes to **finish-change**. Keep the same independent reviewer for corrections.
Follow-up scope is only carried findings, remediation diffs, and regressions against
the frozen contract. A new blocker must be either remediation-caused or a P0/P1 late
violation with a rationale. After two correction cycles, another changes-requested
verdict blocks the change; it never turns into approval or permission to skip review.
