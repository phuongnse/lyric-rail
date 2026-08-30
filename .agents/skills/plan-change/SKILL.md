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
   When new evidence creates multiple valid boundary choices, present them with an
   evidence-backed recommendation and wait for the project owner before registration.
5. Under an adopted `provenance-gated-authored-review` policy, emit bounded plan
   schema 3 provenance. Record the registering actor and immutable authority for an
   authored plan. Use `process-generated` only when the installed core recognizes
   the generator and can exactly recompute the complete plan from its bound
   source-owned inputs; a claimed label or partial comparison grants no bypass.
6. Validate the plan with processctl contract validate --kind plan, then register it
   with processctl change plan. For an authored nontrivial plan under the adopted
   policy, run `processctl change decision start` before a genuinely fresh read-only
   reviewer assesses every canonical material category, then register the assessment
   with `processctl change decision submit`. A `decision-required` assessment remains
   blocked until `processctl change decision resolve` binds the exact approved
   recommendation and explicit owner resolution. Implementation remains blocked
   while sign-off, open decisions, or required plan-decision evidence is missing.

## Hard gates

- Do not edit implementation files during planning.
- Do not omit required security, compatibility, data, accessibility, rollout, or
  rollback work merely to reduce scope.
- Do not invent project commands; select profiles from .process/project.json.
- Do not register a preferred architecture, authority, compatibility, rollout, or
  lifecycle route while the owning decision remains unresolved.
- Do not use risk labels, author self-classification, prose heuristics, or a generator
  claim as implementation authority. Unreviewed prose is candidate-only.

## Output

Return change id, lifecycle phase, ordered work items, acceptance mapping, risks,
open decisions, required profiles, and next owner.
