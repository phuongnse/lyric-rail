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
   If root-cause evidence leaves multiple valid owner, trust, compatibility, rollout,
   or lifecycle corrections, stop and use the run-change owner-decision escalation
   protocol; do not try alternate process architectures until one is accepted.
   Before adding custom standard-format or protocol logic, assess maintained
   dependencies and existing supported tools. Reuse a suitable dependency through the
   managed lock flow; require evidence before owning a custom parser or equivalent
   implementation.
   Treat three changes-requested final-review cycles in one decision window as
   validated repeated friction regardless of renamed or split findings. Do not mint a
   sequence of narrower invariant ids to keep local mutation moving. Let lifecycle
   stop, preserve the complete finding chain, and use the existing owner-decision
   protocol before another correction window. A finding outside a schema-4 finite
   fault row is a contract gap and requires a superseding contract, never an in-place
   local-fix classification.
4. Add regression cases at the lowest reliable owner boundary for both valid behavior
   and the fail-closed class. A shared fix also requires producer profiles and a real
   affected-consumer reproduction before release authorization. For skill behavior,
   run a realistic forward test without providing the expected answer.
5. Treat exit-zero warning or error diagnostics from any process-owned command as a
   validated failure. Preserve their redacted evidence and correct the owning source,
   configuration, dependency, or external boundary; do not add a suppression,
   consumer wrapper, alternate command, or legacy evidence conversion.
6. Validate backward compatibility, version impact, consumer locks, and migration
   needs. Remove superseded guidance instead of preserving duplicate paths.
   Check the full `production-v1` boundary and derive every release identity surface
   from the release contract so a local fix cannot create a new cross-surface drift.
7. Measure whether the change improves task fidelity without disproportionate
   workflow cost.
8. For an operations or external transient, require bounded idempotent recovery with
   retained per-attempt diagnostics. Never mutate source or version merely to retry.
9. For a self-hosted authority rotation, let the old immutable authority govern the
   new authority's introduction, publish the new identity before any consumer pin,
   prove cutover without a control gap, and retire the old authority only after the
   new boundary is active. Keep a clean N-1 control workspace separate from the N+1
   candidate, pre-register the transition, treat target evidence as untrusted, and
   make protected merge the sole activation event. If N-1 predates this route, use
   only the exact source-owned, policy-bound, single-use bootstrap contract. Use
   separate lifecycle changes when provider mechanics make introduction and cutover
   separately publishable.
10. Own the mandatory `improvement-required` phase. For a shared consumer case, export
   one bounded untrusted signal and keep dependent work in `improvement-pending`.
   Producer disposition assigns the canonical catalog invariant and linked lifecycle;
   immutable release resolution and exact consumer reproduction close the chain.

## Hard gates

- Do not promote an agent-host quirk into the portable core.
- Do not reinvent a mature parser, protocol, serializer, cryptographic primitive, or
  platform adapter when a maintained dependency satisfies the accepted contract.
- Do not add a gate without an owner, failure message, and regression proof.
- Do not break a released schema within its major version.
- Do not respond to a rejected process direction by autonomously trying another
  boundary-changing direction.
- Do not let a new trust root approve its own introduction or pin an authority that
  is not yet immutable and resolvable by the consumer.
- Do not treat a signal, disposition, producer completion, or pre-release candidate
  as implementation, merge, release, adoption, or consumer-recovery authority.

## Output

Report classification, changed owner, regression evidence, version impact, affected
consumers, and any rule retired.
