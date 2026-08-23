---
name: change-api
description: Change a public or internal API contract while preserving ownership, authorization, validation, generated artifacts, compatibility decisions, and consumer parity. Use for routes, operations, request or response shapes, status behavior, schemas, or generated clients.
---

# Change an API

## Goal

Evolve one API surface without drifting from its product owner or supported consumers.

## Workflow

1. Read the owning product and architecture contracts, current design decision, API
   description, implementation, consumers, generated artifacts, and tests.
2. Trace operation identity, authorization, request and response fields, validation,
   status behavior, error format, generated clients, and all supported callers.
3. Decide compatibility from explicit consumer and data evidence. Apply the approved
   clean replacement or versioned compatibility strategy consistently.
4. Implement the smallest authoritative contract change. Keep server-owned derived
   values out of caller-authored requests and preserve safe failure mapping.
5. Regenerate every declared artifact from its owner; never hand-maintain generated
   parity. Update consumers in the same slice when a clean replacement was selected.
6. Run focused contract, authorization, validation, status, generation, and consumer
   checks declared by the project.

## Hard gates

- Do not expose a new public or trust-boundary surface without required approval.
- Do not accept both retired and replacement shapes unless compatibility requires it.
- Do not claim parity from source inspection when generated or runtime proof is required.

## Output

Return operation and shape changes, authorization, compatibility, generated artifacts,
consumer updates, evidence, and unresolved decisions.
