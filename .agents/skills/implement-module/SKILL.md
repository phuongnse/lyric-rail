---
name: implement-module
description: Implement approved tactical module patterns for domain behavior, coordination, persistence, read models, concurrency, and messaging. Use only when current acceptance criteria or a ready module design requires those patterns.
---

# Implement a Module

## Goal

Implement and prove only the tactical patterns selected by current product and
architecture contracts.

## Workflow

1. Confirm every requested pattern is required by current acceptance criteria or a
   ready module design. Return undecided foundational choices to design-module.
2. Implement domain invariants, identities, values, lifecycle, and safe failures in
   the project-declared domain boundary.
3. Implement coordination, validation, transactions, repositories, queries,
   deterministic ordering, and concurrency only as selected.
4. Implement messages, delivery, inbox, outbox, or replay only when their operational
   semantics are already decided.
5. Prove behavior at the lowest reliable project boundary and add deterministic
   enforcement only for reusable declared invariants.
6. Return the evidence and remaining architecture decisions to the caller.

## Hard gates

- Do not add tactical patterns for hypothetical future needs.
- Do not leak infrastructure dependencies across a forbidden architecture boundary.
- Do not call a rule enforced without a deterministic mechanism and regression proof.

## Output

Return the implemented pattern set, source boundaries, behavior and persistence
evidence, enforcement changes, and blocked decisions.
