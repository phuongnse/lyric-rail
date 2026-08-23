---
name: build-frontend-foundation
description: Define or evolve reusable product-neutral frontend foundations such as application frames, navigation, route shells, providers, collection infrastructure, and cross-route behavior. Use when several client journeys depend on one non-product-specific contract.
---

# Build a Frontend Foundation

## Goal

Own one reusable frontend contract and its evidence without absorbing product journeys
or the shared visual-system authority.

## Workflow

1. Locate the project-declared foundation owner and identify consumers, activation,
   guarantees, alternate behavior, and out-of-scope product outcomes.
2. Define reusable accessibility, responsiveness, localization, navigation, state,
   and extension guarantees without embedding one consumer's identifiers or copy.
3. Separate product-neutral mechanics from visible cross-feature UI conventions and
   product behavior. Route those decisions to their declared owners.
4. Implement one coherent foundation unit with focused tests and register all active
   consumers through the project's chosen ownership mechanism.
5. Maintain honest lifecycle, acceptance, and evidence state. A checker proves trace
   integrity only; it cannot provide missing design or human acceptance.
6. Run project-declared foundation, frontend, accessibility, and consumer checks.

## Hard gates

- Do not move actor goals or business side effects into a foundation.
- Do not introduce a shared visual API without the UI governance contract.
- Do not mark a foundation enforced while required consumers or evidence are missing.

## Output

Return foundation owner, guarantees, consumers, extension points, implementation,
evidence, lifecycle state, delegated decisions, and gaps.
