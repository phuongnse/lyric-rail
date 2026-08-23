---
name: design-module
description: Decide durable module boundaries, ownership, data, persistence, messages, integration contracts, and operational guarantees before foundational module implementation. Use for a new module or a change to established architecture.
---

# Design a Module

## Goal

Produce a ready or blocked architecture decision that tactical implementation can
follow without reopening foundational ownership.

## Workflow

1. Read the owning product contract, project architecture, enforcement status, and
   current design assessment.
2. Define language, lifecycle and data ownership, mutation authority, upstream inputs,
   downstream contracts, composition boundary, and forbidden dependencies.
3. Decide invariants, requests, validation, authorization, tenancy, idempotency,
   concurrency, and business-failure mapping where applicable.
4. Decide persistence, migration, transaction, query, rollback, and operational
   boundaries from current requirements rather than hypothetical reuse.
5. Define message delivery, ordering, versioning, replay, retention, and rebuild only
   for message or event patterns actually in scope.
6. Name deterministic enforcement and proving evidence, then return ready or blocked.

## Hard gates

- Do not place product behavior in a shared module without an explicit contract.
- Do not choose event sourcing or another high-operational-cost pattern without the
  required project approval and complete operational decisions.
- Do not implement a foundational module while its architecture is blocked.

## Output

Return readiness, boundary, ownership, invariants, persistence, integration, events,
operations, enforcement, evidence, and adopted tactical patterns.
