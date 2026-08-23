---
name: define-change-contract
description: Specify and register scope, acceptance outcomes, comparison base, risk, sign-off, affected repositories, and required verification before engineering implementation begins. Use for feature, bug, architecture, contract, migration, security, or cross-project work whose completion boundary is not already explicit.
---

# Define a Change Contract

## Goal

Establish one evidence-backed change boundary before implementation without
inventing product behavior or duplicating the owning specification.

## Workflow

1. Read the nearest AGENTS.md and the project sources it identifies as authoritative.
2. Trace affected callers, consumers, contracts, tests, generated artifacts, and
   repositories only far enough to define the real blast radius.
3. Record a stable change id, concise summary, source request, comparison base,
   affected projects, measurable acceptance outcomes, risk, required verification
   profiles, and sign-off state. Link the project-owned specification when one owns
   product behavior; otherwise state why the change contract is sufficient.
4. Assess every `production-v1` dimension and every declared project extension.
   Map each applicable dimension to measurable acceptance criteria; record a concrete
   rationale and no criteria for a dimension that is genuinely not applicable.
5. Classify risk as low, medium, or high using project policy. When policy requires
   sign-off, stop implementation until approval evidence exists.
6. Validate the document with processctl contract validate --kind change, then
   register it with processctl change start. Registration completes specification;
   it does not authorize implementation.
7. Hand the registered contract to planning. Keep durable product decisions in their
   project-owned specification rather than in process history.

## Hard gates

- Do not replace missing product decisions with assumptions.
- Do not lower required security, compatibility, migration, accessibility, or
  evidence boundaries to keep moving.
- Do not mark a quality dimension not applicable merely because its work was omitted.
- Report missing or blocked evidence honestly.

## Output

Return the registered change id, validated contract, unresolved decisions, sign-off
state, lifecycle phase, and next owner.
