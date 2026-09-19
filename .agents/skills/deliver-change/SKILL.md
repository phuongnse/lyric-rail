---
name: deliver-change
description: Drive a repository change through the governed engineering lifecycle, from an accepted contract to independently reviewed completion.
---

# Deliver a change

## Route card

Use `processctl change status --change-id ID` as the state authority. The phase tells
you the only next lifecycle command; the accepted contract and plan tell you the
scope and evidence boundary.

Status is the reader's first entry point. Its `lifecycleStatus` is the state-machine
phase; `candidate` is the current source checkpoint; `contract.digest` and
`plan.digest` identify the accepted inputs; `evidence` is the current
schema-validated verification selection; and `nextAction` is the recommended
operator route. Read `evidence.requirements` rather than the recorded report status:
`satisfied` means the stored report is reusable for the current inputs,
`remaining` means execution is required, `unknown` means identity is insufficient,
`blocked` means a consumer or owner action is required, and `inapplicable` means an
optional profile was not selected. A stored `passed` report is not a current pass
until the selection says `satisfied`. `recordedVerification` is historical detail
only. If a profile is failed, `evidence.diagnostics` gives its current/stale/
unavailable classification and `diagnostics` gives the validated failed check,
failure class, bounded execution facts, and fixed selective reproduction command;
it never authorizes a retry or substitutes for the required profile. The existing
`verification` field keeps its report-status meaning (`passed` or `failed`);
`currentVerification` is the additive projection of the current selection. Review
state includes any active blocking findings, and `nextAction` includes the required
actor/context/plan/report inputs or an explicit placeholder when the caller must
choose them.
For a completed change, `cleanup.status` is part of the result boundary: `clean` is
terminal for the process, while `pending` or `failed` routes back through `change
finish` so cleanup can be retried without recreating implementation evidence.

For the documentation route, use the repository's reader map and
docs/documentation.md. Documentation impact is part of the ordinary phase work:
identify affected readers and the authoritative source at start/plan, update
consumer-owned knowledge during implementation when needed, verify usable output,
and review accuracy and findability. Do not create a second documentation
lifecycle, a universal document inventory, or a checkbox that says only that
documentation was updated.

Use these terms precisely when explaining a result:

- A candidate checkpoint is the source state being assured (head plus bounded file
  fingerprint); it is not a runtime fingerprint.
- An execution identity fingerprints the process runtime, dependencies, and managed
  child environment used for reuse decisions. It does not isolate execution.
- The bounded runner provides timeout, output, cleanup, and child-containment
  boundaries. Those controls are execution safety, not proof that two runtimes are
  identical.
- Schema/protocol validation proves a document has an accepted typed structure. It
  does not infer open-ended meaning from keywords, names, paths, or diagnostics.
- Independent review is a separate actor and context exercising judgment over the
  exact candidate. It is not authenticated identity and a coordinator cannot replace
  its verdict or report.

| Current state | Do now | Evidence consumed or produced | Next |
| --- | --- | --- | --- |
| no run | start | accepted contract, consumer evidence, readiness | `specified` → plan |
| specified | plan | contract digest, affected paths, invariant assessments | `planned` → implement |
| planned / changes-requested | implement | implementation identity and in-scope diff | `implementing` → verify |
| implementing | verify | required profiles on one unchanged candidate | `verified` → independent review |
| verified / review-pending | review | fresh independent reviewer, exact checkpoint, dispositions | `approved` or correction |
| approved | complete | current checkpoint, profiles, review, receipt | owner-controlled release/adoption |

If the candidate is outside the frozen plan, stop and supersede the contract or plan;
do not widen a directory or replace missing evidence with prose.

Use this as the only entry point for delivery work. Run `processctl project validate
--json` first. When readiness is present, report its stage, immutable pack versions,
enforced floor, and planned gaps. Planned gaps guide future work but do not become the
scope of the current change unless the accepted request selects one; never choose the
product roadmap autonomously.

The six **change-*** skills are lifecycle routes selected here. Inspect processctl
change status when a change already exists, then route exactly one current phase:

1. No run: use **change-start**.
2. specified: use **change-plan**.
3. planned or changes-requested: use **change-implement**.
4. implementing: use **change-verify**.
5. verified or review-pending: hand off to an actual independent reviewer through
   **change-review**, resuming the existing assignment when review is pending; if the
   repository changed after evidence was recorded, use **change-implement** to open a
   new cycle.
6. approved: use **change-complete**; a later repository change also reopens through
   **change-implement**.
7. blocked: stop. The current contract cannot merge; the owner may narrow or
   supersede it, but no correction-limit stop can waive independent review.

An active run can be handed to a sequential workspace only through the explicit
package commands below. Export requires a committed candidate; import validates the
same process authority, comparison base, checkpoint, changed paths, and change id and
never overwrites conflicting runtime state:

    processctl change handoff export --change-id ID --output HANDOFF_PATH
    processctl change handoff import --handoff HANDOFF_PATH

The package is a transport boundary, not a second lifecycle or evidence store. It
preserves the active run for implementation, review, or approved coordination; the
normal independent reviewer and freshness rules still apply after import.

If implementation finds candidate paths outside the frozen plan, the lifecycle records
the paths as a `plan-scope` blocker and stops before verification or approval. Do not
edit the accepted plan, widen a directory to satisfy the check, or retry the same
operation. The owner may preserve the accepted outcome by preparing a new current-v1
contract with `supersedes: {"changeId": "...", "reason": "missing-plan-boundary"}`;
`change start` then records the prior run's path/digest and exact comparison base. The
new plan must cover the complete inherited candidate diff. Prior findings, approvals,
verification, and correction limits are never copied; a changed outcome needs a fresh
owner decision and ordinary new contract.

A failed required command is a different condition. Its report remains attached to the
same run and exposes only safe structured execution metadata and the fixed selective
reproduction descriptor. `change explain` labels that diagnostic `current`, `stale`, or
`unavailable`; `change verify --remaining` does not retry a failed profile on the same
candidate and input identity. Use an explicit profile refresh only after a concrete
consumer/input action, and keep its result distinct from reused evidence. A spawn or
other execution-condition error records the missing consumer action without persisting
raw process errors. Blockers in the lifecycle are separate from consumer failures and
from correction limits or any goal/coordination harness state.

When changing this process itself, first use **process-improve** to prove the request
came from a real consumer incident or need; the change still follows the same six
phases afterward.

Use **production-engineering** design guidance when defining acceptance criteria for
material changes. From planning through independent review, also apply its shared
correctness floor. Its canonical definitions drive the invariant assessments; do not
replace contextual judgment with keyword or naming heuristics.

The project's nearest AGENTS.md owns product decisions and .process/project.json owns
exact argument-array commands. Skills guide the work; only processctl advances
lifecycle state. Never replace missing, stale, failed, or self-authored evidence with
a prose claim.

## Preserve agent execution settings

Keep the user-selected model and reasoning effort unchanged throughout the current
task, including every delegated agent and independent reviewer. The active parent
task owns this selection. Only an explicit user change establishes a new selection;
do not infer one from an older run, child session, role, skill, global default, or a
model's perceived cost or capability. This rule compares settings for equality and
does not assign named models to roles.

Before spawning or resuming an agent, read the active task's effective settings.
Each new change's independent review starts in a new session without inherited
implementation or other-change review history. Resume a reviewer only within that
same accepted change; preserving settings never requires preserving an old context.
Use inheritance when it preserves both settings; otherwise pass that exact pair
through the native runtime's supported controls. Resuming a child may retain its
old settings, so check it again even when its reviewer identity is unchanged.
Never autonomously upgrade, downgrade, or fall back to another model or effort.

Verify the child's effective settings from native runtime metadata for every
contributing turn before accepting its result. Retain the native handle and settings
evidence with the existing run handoff. A requested configuration or the agent's own
claim is not runtime confirmation. On mismatch, stop the affected agent and correct
its settings before continuing; if the settings are unavailable or unsupported, stop
the handoff and report the limitation instead of accepting an unverified result.

This is a portable agent instruction. The host runtime owns enforcement and settings
observations; processctl does not select provider models or certify their quality.
Preserving settings does not waive independent actor/context or snapshot checks.

Report the change id, phase, cycle, current evidence, blocker, and next command.
Also report readiness capabilities affected or intentionally advanced by this change;
do not describe `building` as production or infer promotion from prose.
