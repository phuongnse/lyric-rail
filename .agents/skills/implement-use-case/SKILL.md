---
name: implement-use-case
description: Implement a planned product slice from an approved use-case contract, preserving layer ownership, acceptance traceability, generated artifacts, and honest status. Use as a domain overlay within the shared implementation lifecycle.
---

# Implement a Use Case

## Goal

Deliver one coherent product slice whose source and tests trace to approved behavior.

## Workflow

1. Read the registered change, plan, owning use case, applicable foundation and
   architecture contracts, and affected project instructions.
2. Map each in-scope acceptance criterion to work items and its lowest reliable test
   boundary before editing.
3. Implement in dependency order defined by the project architecture. Stop when a
   required lower boundary or owner decision is unresolved.
4. Keep adapters thin, generated contracts authoritative, business failures explicit,
   and compatibility aligned with the approved design decision.
5. Update tests, generated artifacts, acceptance evidence, and product status in the
   same slice. Remove retired callers and guidance when overlap is not required.
6. Run focused project commands during implementation, then return to shared
   verification, independent review, and completion gates.

## Hard gates

- Do not treat product evidence as a replacement for lifecycle verification.
- Do not mark behavior complete without current evidence for required outcomes.
- Do not broaden the planned product scope without updating its contract and plan.

## Output

Return implemented scope, acceptance mapping, source and generated artifacts, tests,
status changes, delegated decisions, deferrals, and remaining gaps.
