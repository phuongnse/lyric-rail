---
name: finish-change
description: Complete an independently approved engineering change only when its checkpoint, verification, review, findings, and workspace remain current. Use after review approval and before any completion, merge-readiness, release-readiness, or delivery claim.
---

# Finish a Change

## Goal

Close the lifecycle with an auditable completion record without publishing, merging,
or deploying implicitly.

## Workflow

1. Read processctl change status and require the approved phase.
2. Confirm the current clean checkpoint and workspace fingerprint match the approved
   review and every required verification report.
3. Confirm every acceptance criterion and applicable quality dimension is covered,
   every required finding is resolved, required sign-off is current, and
   project-owned status or evidence is reconciled.
4. Run processctl change finish. Treat any stale artifact, source change, missing
   profile, or identity mismatch as a blocker that returns to the owning phase.
5. Report completion separately from publication, merge, release, or deployment.
   Perform those operations only through an explicitly authorized project workflow.

## Hard gates

- Do not finish a changes-requested, merely verified, or review-pending change.
- Do not reuse approval after the source checkpoint or workspace changes.
- Do not infer release or deployment authorization from process completion.

## Output

Return change id, completed checkpoint, comparison base, verification reports,
independent-review report, completion record, and any separate publication owner.
