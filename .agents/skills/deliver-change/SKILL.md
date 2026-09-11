---
name: deliver-change
description: Drive a repository change through the governed engineering lifecycle, from an accepted contract to independently reviewed completion.
---

# Deliver a change

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
