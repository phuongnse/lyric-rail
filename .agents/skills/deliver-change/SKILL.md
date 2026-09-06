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
5. verified or review-pending: use **change-review**, resuming the existing assignment
   when review is pending; if the repository changed after evidence was recorded,
   use **change-implement** to open a new cycle.
6. approved: use **change-complete**; a later repository change also reopens through
   **change-implement**.
7. blocked: stop. The current contract cannot merge; the owner may narrow or
   supersede it, but no correction-limit stop can waive independent review.

When changing this process itself, first use **process-improve** to prove the request
came from a real consumer incident or need; the change still follows the same six
phases afterward.

From planning through independent review, use **production-engineering** as the
shared correctness floor. Its canonical definitions drive the plan and review
contracts; do not replace their contextual assessment with keyword or naming
heuristics.

The project's nearest AGENTS.md owns product decisions and .process/project.json owns
exact argument-array commands. Skills guide the work; only processctl advances
lifecycle state. Never replace missing, stale, failed, or self-authored evidence with
a prose claim.

Report the change id, phase, cycle, current evidence, blocker, and next command.
Also report readiness capabilities affected or intentionally advanced by this change;
do not describe `building` as production or infer promotion from prose.
