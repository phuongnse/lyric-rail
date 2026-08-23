---
name: plan-change
description: Build and register an executable implementation plan from a specified change contract. Use after change specification and required sign-off, before editing implementation files for any non-trivial engineering change.
---

# Plan a Change

## Goal

Convert accepted outcomes and project policy into one bounded, reviewable execution
plan without changing implementation source.

## Workflow

1. Read lifecycle status, the registered change contract, nearest AGENTS.md, and only
   the project owners needed for the affected surface.
2. Trace callers, consumers, trust boundaries, tests, migrations, generated outputs,
   documentation, and retirement work far enough to define complete work items.
3. Record ordered work items with owned paths and verification profiles. Map every
   acceptance criterion and applicable production-quality dimension to at least one
   work item and project-owned profile.
4. Record concrete risks and mitigations. Leave behavior-changing ambiguity as an
   open decision; do not plan around it.
5. Validate the plan with processctl contract validate --kind plan, then register it
   with processctl change plan. Implementation remains blocked while required
   sign-off or open decisions remain.

## Hard gates

- Do not edit implementation files during planning.
- Do not omit required security, compatibility, data, accessibility, rollout, or
  rollback work merely to reduce scope.
- Do not invent project commands; select profiles from .process/project.json.

## Output

Return change id, lifecycle phase, ordered work items, acceptance mapping, risks,
open decisions, required profiles, and next owner.
