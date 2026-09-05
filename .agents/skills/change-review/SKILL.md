---
name: change-review
description: Review the exact verified snapshot from an independent actor and context when routed by deliver-change.
---

# Review a change

The reviewer must not share either actor identity or execution context with an
implementer in the current cycle. Read `processctl change status --change-id ID`.
When the phase is `verified`, start the assignment:

    processctl change review start --change-id ID --actor REVIEWER --context REVIEW_CONTEXT

When the phase is `review-pending`, resume the existing assignment; do not run
`change review start` again. Read `.process/runs/ID/run.json` for its `cycle` and
`reviewAssignment`, including the assigned reviewer, checkpoint, and
`reportSchemaVersion`. Continue with the assigned independent actor/context and the
existing report path, `.process/runs/ID/review-CYCLE.json`. If that reviewer is
unavailable, report the pending assignment as a blocker; never impersonate its
identity or create a replacement assignment from another context.

Review the accepted contract, plan, complete diff, focused tests, and verification
evidence. The first pass is comprehensive within that frozen contract. Every finding
maps to one accepted criterion and records priority, origin, severity, and location.
Priority records impact if the finding remains unresolved; severity controls the
current lifecycle gate and is not derived mechanically from priority. Ideas outside
the contract are proposals, not blocking findings. approved may contain non-blocking
observations but no blocking finding; changes-requested requires at least one blocking
finding.

Read **production-engineering** and independently reassess every canonical invariant.
Use the report's `productionEngineering` entries to record `satisfied`,
`not-applicable`, or `violated`; cite concrete snapshot evidence for each satisfied
entry. A violated entry links to a distinct blocking finding whose origin records the
production invariant, unless a correction-cycle regression or critical late finding
already supplies the bounded origin. The plan's applicability decision is evidence,
not authority: correct it in the review result when the complete diff proves
otherwise.

Use the `reportSchemaVersion` and `reportPath` returned by review start. In schema
versions 6 and later, every non-blocking finding has a disposition and rationale;
never omit an observation merely to reach approval. `resolved` records why the
reviewed snapshot closes it. `accepted-risk` and `tracked-follow-up` also record an
owner and stable HTTPS `recordUrl`. The report path is process state and does not
mutate the reviewed snapshot.

Review start also returns bounded `processSignals` derived from exact lifecycle
events. Treat them as prompts for judgment, not proof of a shared defect; independently
consider consumer evidence that the lifecycle cannot observe. Every schema-version 7
report classifies `processImprovement` as `none`, `consumer-specific`, or
`shared-process` and gives a concrete rationale. Consumer-specific behavior stays in
the consumer. For `shared-process`, keep the assignment `review-pending` and route the
candidate through **process-improve**. Submit only after an existing or owner-authorized
issue supplies the stable HTTPS `recordUrl`; the review itself remains read-only.

Read the consumer readiness result and repository rules. Check the complete diff for
an affected enforced capability omitted from the contract, weakened evidence, a pack
version changed implicitly, or a planned gap made blocking without accepted scope.
For a planned-to-enforced transition, require the explicit readiness diff and current
consumer-owned evidence; reject promotion by prose, stale evidence, or renamed gap.
Do not block the change merely because unrelated planned capabilities still exist.

Validate and submit the report:

    processctl contract validate --kind review REPORT_PATH
    processctl change review submit --change-id ID --review REPORT_PATH

Review is read-only. Requested changes route back to **change-implement**; approval
routes to **change-complete**. Keep the same independent reviewer for corrections.
Follow-up scope is only carried findings, remediation diffs, and regressions against
the frozen contract. A new blocker must be either remediation-caused or a P0/P1 late
violation with a rationale. After two correction cycles, another changes-requested
verdict blocks the change; it never turns into approval or permission to skip review.

## Finding priority

P0-P3 are this process's review-impact convention. Assess the consequences if the
finding remains unresolved in a supported scenario. Consumer scheduling and incident
response deadlines remain consumer-owned.

| Priority | Impact | Illustrative example |
| --- | --- | --- |
| P0 - critical | Catastrophic loss of essential operation, authoritative data, or a critical security guarantee. | Irrecoverable loss of the only authoritative dataset. |
| P1 - high | Severe failure of a core workflow or guarantee, below P0's catastrophic consequences. | A primary workflow produces wrong results that can be regenerated from intact inputs. |
| P2 - medium | Material but contained incorrect behavior or operational or maintenance cost; core workflows remain usable. | A recoverable failure in a secondary workflow with a safe alternative. |
| P3 - low | Minor inconvenience or clarity or consistency defect with little effect on behavior or reliability. | An unclear optional explanation when all required steps remain unambiguous. |

Choose the highest level whose consequences the evidence supports. Explain affected
guarantees, reach, preconditions, recoverability, and safe alternatives. Examples are
illustrative, not an exhaustive classifier; file type, finding category, keywords,
and ease of repair do not determine priority.

Severity follows whether the reviewed snapshot violates the accepted contract or an
applicable production invariant; priority is not a waiver. A P2 or P3 finding can
therefore be blocking. The existing P0/P1 restriction on a new `critical-late` blocker
is an eligibility condition, not permission to block without the required bounded
late rationale. Preserve carried finding priorities and historical reports under the
existing correction-cycle rules.
