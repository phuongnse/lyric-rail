---
name: assess-design
description: Establish the owner, risk, production concerns, compatibility decision, blast radius, and required approval for a non-trivial engineering change before implementation. Use when a change affects architecture, contracts, trust boundaries, persistence, tooling, workflows, or several product surfaces.
---

# Assess a Design

## Goal

Produce a bounded design decision that implementation can follow without inventing
product behavior or reopening foundational choices.

## Workflow

1. Read the change contract, nearest project instructions, owning product or
   architecture contracts, and current implementation evidence.
2. Identify the decision owner, affected consumers, data, trust boundaries,
   generated artifacts, migrations, operational concerns, and rollback boundary.
3. Classify risk using the project policy. Treat an absent policy as a gap rather
   than inventing a local tier.
4. Decide whether compatibility is required from supported consumers and data. When
   it is not required, define a clean replacement and removal scope.
5. Map every applicable production concern to an owner and proof. Keep unrelated
   future capabilities out of scope without weakening the implemented boundary.
6. Define implementation units, stop conditions, focused evidence, review evidence,
   and required human decisions. Record durable decisions in their project owner.
7. If evidence supports multiple materially different owners, trust boundaries,
   compatibility choices, rollout paths, or lifecycle sequences, enumerate the hard
   invariants and assumptions before comparing options. Create a recommendation
   artifact whose classifications and complete valid set are derived by processctl;
   apply cost or minimal-change optimization only within that valid set.
8. Run `processctl recommendation review start` to reserve an unused project-global
   context and bind the exact reviewer assignment before an independent adversarial
   review begins. Present a high-risk recommendation only after `processctl
   recommendation validate-chain` approves the exact assignment-bound chain. If no
   valid option exists, report the missing evidence or authority instead of
   recommending an invalid or unproven compromise.
9. Record the owner's selected valid option in a recommendation resolution. Treat the
   resolution as decision evidence, never as lifecycle, merge, release, deployment,
   or adoption authority.
10. Return ready only when blocking decisions and required approvals are resolved.
    Treat every unreviewed design paragraph, generated summary, or conversational
    preference as candidate-only; prose becomes decision authority only through its
    governing reviewed artifact and owner resolution.

## Hard gates

- Do not edit implementation before a required design decision or approval.
- Do not replace a required owner, trust boundary, invariant, or evidence boundary
  merely to bypass a failure.
- Do not rank cost, convenience, minimal change, or rollout speed before hard
  invariant validity, or present an unreviewed high-risk recommendation.
- Do not preserve obsolete compatibility paths without a supported requirement.
- Do not turn an unresolved project-owner decision into an implementation assumption.

## Output

Return readiness, risk, owners, governing contracts, blast radius, production
fitness, compatibility, implementation units, evidence, approvals, and blockers.
