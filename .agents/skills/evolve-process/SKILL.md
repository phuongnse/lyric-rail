---
name: evolve-process
description: Improve or retire engineering process guidance from validated defects, escaped gates, false positives, review findings, or repeated friction. Use when evidence suggests a local fix, reusable rule, deterministic checker, schema change, or obsolete process behavior.
---

# Evolve the Process

## Goal

Change the smallest correct owner and prove the affected class without turning
incident history into permanent ceremony.

## Workflow

1. Start from a reproducer, escaped defect, false positive, review finding, or measured
   workflow cost. Do not generalize from speculation alone.
2. Apply the run-change failure-to-invariant protocol. Classify the owning boundary as
   project-local, shared-process, operations-or-external, or missing product or
   authorization input before corrective mutation; then classify the reusable class
   as a process rule, deterministic invariant, portability gap, or obsolete rule.
3. Fix local behavior in the project owner. When the defect belongs to shared process
   semantics, keep the consumer candidate blocked and remove any provisional consumer
   wrapper or duplicate implementation. Change shared skills only for portable
   semantics. Add CLI or schema enforcement only when deterministic.
4. Add regression cases at the lowest reliable owner boundary for both valid behavior
   and the fail-closed class. A shared fix also requires producer profiles and a real
   affected-consumer reproduction before release authorization. For skill behavior,
   run a realistic forward test without providing the expected answer.
5. Validate backward compatibility, version impact, consumer locks, and migration
   needs. Remove superseded guidance instead of preserving duplicate paths.
   Check the full `production-v1` boundary and derive every release identity surface
   from the release contract so a local fix cannot create a new cross-surface drift.
6. Measure whether the change improves task fidelity without disproportionate
   workflow cost.
7. For an operations or external transient, require bounded idempotent recovery with
   retained per-attempt diagnostics. Never mutate source or version merely to retry.

## Hard gates

- Do not promote an agent-host quirk into the portable core.
- Do not add a gate without an owner, failure message, and regression proof.
- Do not break a released schema within its major version.

## Output

Report classification, changed owner, regression evidence, version impact, affected
consumers, and any rule retired.
