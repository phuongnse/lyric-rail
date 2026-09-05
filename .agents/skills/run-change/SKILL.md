---
name: run-change
description: Route a non-trivial repository change through the complete engineering process from an accepted contract to independently reviewed completion.
---

# Run a change

Use this as the only entry point for delivery work. Run `processctl project validate
--json` first. When readiness is present, report its stage, immutable pack versions,
enforced floor, and planned gaps. Planned gaps guide future work but do not become the
scope of the current change unless the accepted request selects one; never choose the
product roadmap autonomously.

Inspect processctl change status when a change already exists, then route exactly one
current phase:

1. No run: use **start-change**.
2. specified: use **plan-change**.
3. planned or changes-requested: use **implement-change**.
4. implementing: use **verify-change**.
5. verified or review-pending: use **review-change**; if the repository changed after
   evidence was recorded, use **implement-change** to open a new cycle.
6. approved: use **finish-change**; a later repository change also reopens through
   **implement-change**.
7. blocked: stop. The current contract cannot merge; the owner may narrow or
   supersede it, but no correction-limit stop can waive independent review.

When changing this process itself, first use **improve-process** to prove the request
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
