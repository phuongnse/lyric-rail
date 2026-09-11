---
name: change-review
description: Review the exact verified snapshot from an independent actor and context when routed by deliver-change.
---

# Review a change

The reviewer must not share either actor identity or execution context with an
implementer in the current cycle. An implementer must hand off this phase to an
actual reviewer, not perform it under another identity.

For every new change, spawn a new reviewer agent/session without the implementation
conversation or another change's review conversation. Do not resume an earlier
change's reviewer merely to preserve model settings; a fresh session must keep the
same active user-selected model and effort. Reuse a reviewer handle only for the
same accepted change and its correction cycles.

Supply the accepted contract and plan, recorded `comparisonBaseCommit`, candidate
path/checkpoint, and verification evidence. Request an independent assessment of
the complete diff and relevant code/tests without suggesting a verdict. Follow
**deliver-change**'s agent execution settings rule for the spawn and verify the
native runtime's effective settings before accepting the review. The provider or
account may be shared; actor and execution context must remain independent.
If the environment cannot run or reach an
independent reviewer, leave the change awaiting review and report that missing
handoff.

Use `comparisonBaseCommit` from lifecycle output or the run state; never re-resolve
the original moving ref after implementation. Older runs may lack this field. Keep
their accepted comparison boundary explicit and establish the complete diff from
available history; if it cannot be established, request an owner-selected replacement
contract rather than claiming the base was pinned at start.

Use the runner's returned reviewer handle to continue the real reviewer. Record its
actor/context in the existing assignment and retain the native task/session
interaction and returned result for inspection. Distinct identity strings alone do
not demonstrate that a review ran.

Before assignment, retain native evidence that this session was created for this
change and dispatched without inherited implementation history: the creation record,
parent dispatch and successful result binding, plus effective model/effort observations
for contributing turns. A new actor name, `fresh=true`, or an agent-supplied timestamp
is not that evidence. If the host cannot establish it, report the missing handoff.

Read `processctl change status --change-id ID`.
When the phase is `verified`, start the assignment:

    processctl change review start --change-id ID --actor REVIEWER --context REVIEW_CONTEXT

When the phase is `review-pending`, resume the existing assignment; do not run
`change review start` again. Read `.process/runs/ID/run.json` for its `cycle` and
`reviewAssignment`, including the assigned reviewer, checkpoint, and
`reportSchemaVersion`. Continue with the assigned independent actor/context and the
existing report path, `.process/runs/ID/review-CYCLE.json`. If that reviewer is
unavailable, report the pending assignment as a blocker; never impersonate its
identity or create a replacement assignment from another context.

The core rejects an agent context recorded in another accepted change in this Git
repository or its registered worktrees. It reads canonical run history with bounded
I/O and schema validation; unsafe, inaccessible or oversized history fails explicitly.
An absent conflict only covers that inspected history. It cannot certify native
freshness, deleted history, other clones, other repositories or inherited conversations.

One narrow recovery exists for an initial pending assignment that the core proves
reused another change's context, before any submitted review or normal report file:

    processctl change review replace-reused --change-id ID --actor NEW_REVIEWER --context NEW_CONTEXT

First stop the wrongly assigned agent and spawn a fresh reviewer for this change.
The command validates the new identity and current evidence, preserves the entire
previous assignment and its recorded conflict in existing history, and leaves the
cycle and verification unchanged. It cannot replace a valid assignment, a stale
snapshot, or a reviewer from a submitted/correction round. Late reports from the old
identity are rejected. This exception does not permit ordinary reviewer substitution.

Apply the same **deliver-change** settings rule when resuming this reviewer. An
existing session handle does not establish that its current model and effort still
match the active user-selected task.

Review the accepted contract, plan, complete diff, focused tests, and verification
evidence. The first pass is comprehensive within that frozen contract. Every finding
maps to one accepted criterion and records priority, origin, severity, and location.
Priority records impact if the finding remains unresolved; severity controls the
current lifecycle gate and is not derived mechanically from priority. Ideas outside
the contract are proposals, not blocking findings. approved may contain non-blocking
observations but no blocking finding; changes-requested requires at least one blocking
finding.

Carry every previously open blocking finding into the next report with its identity
unchanged. It must remain blocking or have a `resolved` disposition with the reason
the reviewed snapshot closes it. Omission, `accepted-risk`, and `tracked-follow-up`
cannot retire a blocker. This also applies to older report versions; their ordinary
non-blocking observations keep the existing compatibility rules.

Assess accepted design criteria separately from passing checks. Use
**production-engineering** design guidance to trace a significant behavior and a
concrete maintenance scenario grounded in current requirements through the affected
code, callers, and dependencies. Evaluate both the effort to understand the flow and
the reach of a change. A design finding must identify the violated criterion, source
location, and concrete correctness, comprehension, or maintenance consequence.
Apply the existing blocking rules to demonstrated violations even when tests pass;
preference for a pattern, shorter code, or a different valid structure is insufficient.
Do not retrofit new design criteria into the frozen contract.

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

The reviewer authors the verdict, findings, and assessments, then validates and
submits its report. If only the coordinator can submit, it transports the reviewer's
returned report unchanged. Report errors go back to the assigned reviewer for
correction; the coordinator must not fill in or rewrite the review content.

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
