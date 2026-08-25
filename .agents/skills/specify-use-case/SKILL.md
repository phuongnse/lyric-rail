---
name: specify-use-case
description: Create or repair an implementation-independent product use-case contract with actors, outcomes, flows, acceptance criteria, test boundaries, decisions, and scope. Use when requested behavior is missing, ambiguous, contradictory, or not ready for implementation.
---

# Specify a Use Case

## Goal

Produce a testable product contract without inventing behavior or mixing current
implementation details into expected outcomes.

## Workflow

1. Locate the project-declared product owner and related contracts, code, tests, and
   vocabulary. Surface conflicts instead of resolving them silently.
2. Define the primary actor goal, preconditions, trigger, success and minimal
   guarantees, main flow, alternate and failure flows, and explicit boundaries.
3. Keep behavior observable and implementation-independent. Link shared technical
   constraints to their architecture owner instead of copying them.
4. Write cohesive acceptance criteria and map each to the lowest reliable acceptance
   boundary. Keep unresolved behavior as a decision, not a required test.
5. Define compatibility only from supported consumers and data. Record an intentional
   clean replacement when overlap is not required.
6. Validate the contract with project-declared documentation and link checks, then
   register the approved scope through the shared change lifecycle.

## Hard gates

- Do not invent identifiers, authorization, integrations, data, or product behavior.
- Do not begin implementation while a behavior-changing decision remains blocking.
- Do not encode framework or storage choices unless interoperability is the outcome.

## Output

Return owner, readiness, actors, flows, acceptance scope, test boundaries, decisions,
compatibility, validation evidence, and blockers.
