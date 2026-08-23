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
7. Return ready only when blocking decisions and required approvals are resolved.

## Hard gates

- Do not edit implementation before a required design decision or approval.
- Do not replace a required owner, trust boundary, invariant, or evidence boundary
  merely to bypass a failure.
- Do not preserve obsolete compatibility paths without a supported requirement.

## Output

Return readiness, risk, owners, governing contracts, blast radius, production
fitness, compatibility, implementation units, evidence, approvals, and blockers.
