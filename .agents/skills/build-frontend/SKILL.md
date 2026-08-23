---
name: build-frontend
description: Implement a complete client journey from a ready product contract, including routes, state, forms, API consumers, accessibility, responsive behavior, recovery, and user-visible loading, empty, error, and success states.
---

# Build a Frontend Journey

## Goal

Deliver one coherent client outcome using the project's authoritative API and UI
contracts with observable, accessible, and recoverable behavior.

## Workflow

1. Read the product outcome, acceptance rows, frontend architecture, active UI
   contracts, generated API surface, and existing route and state owners.
2. Trace entry and exit, neighboring journeys, permissions, URL and server state,
   cache behavior, localization, generated types, and current tests.
3. Define the primary and recovery paths, modes, visible state model, hierarchy,
   content ownership, responsive behavior, keyboard behavior, and accessibility.
4. Implement the complete planned journey narrowly. Use generated wire types and the
   project's enforced UI contracts; do not duplicate server-owned reference data.
5. Remove retired routes, state, mappings, translations, tests, and fallback behavior
   when the approved design selected a clean replacement.
6. Map each acceptance outcome to focused component, integration, or browser evidence
   and run the declared project commands.

## Hard gates

- Do not define a new shared visual convention inside a feature.
- Do not use fixed waits, hidden errors, or broad end-to-end tests as substitutes for
  focused state and behavior evidence.
- Do not hand-write a parallel API contract when generated types are authoritative.

## Output

Return journey, states, routes, UI and API contract usage, acceptance evidence, tests,
accessibility and responsive proof, removals, and gaps.
