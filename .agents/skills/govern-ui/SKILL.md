---
name: govern-ui
description: Define, review, verify, enforce, or replace shared UI contracts, semantic roles, tokens, surface archetypes, component ownership, visual evidence, and conformance policy. Use when a change affects cross-feature visual language or reusable interaction composition.
---

# Govern a UI System

## Goal

Maintain one enforceable UI contract per shared visual or interaction decision while
features retain product state and content ownership.

## Workflow

1. Declare one coherent review unit with one owner, contract, invalidation set, and
   rollback boundary. Obtain the required design decision before implementation.
2. Inventory representative surfaces and define semantic roles across color,
   typography, spacing, density, elevation, motion, layout, interaction, feedback,
   responsiveness, and accessibility.
3. Keep reusable values in the project-declared theme or token owner and expose typed
   semantic contracts to consumers. Avoid policy based on filenames or source text.
4. Define ownership, allowed composition, consumer registration, acceptance criteria,
   evidence kinds, required modes, invalidation triggers, and retirement behavior.
5. Implement only the declared unit. Prove types, rendered ownership, behavior,
   accessibility, responsive modes, and visual evidence through project commands.
6. Keep machine lifecycle and coverage state honest. Human acceptance cannot be
   inferred from deterministic checks, and accepted evidence must be invalidated by
   declared contract, theme, owner, consumer, or evidence changes.

## Hard gates

- Do not refresh a baseline merely to silence unexplained drift.
- Do not mark an unproved requirement covered or claim standards compliance without
  criterion-level evidence.
- Do not leave a retired composition on the supported path after a clean replacement.

## Output

Lead with review-unit state and decision needed; retain owners, consumers, semantic
decisions, modes, coverage, evidence, acceptance, invalidation, and retirement audit.
