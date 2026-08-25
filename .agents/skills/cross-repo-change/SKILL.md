---
name: cross-repo-change
description: Coordinate one engineering change across independent repositories connected by public contracts or versioned dependencies. Use when two or more repositories require ordered checkpoints, compatibility decisions, release coordination, and separate verification.
---

# Coordinate a Cross-Repository Change

## Goal

Keep cross-repository delivery explicit and reproducible without making one
repository depend on another repository's internal process files.

## Workflow

1. Identify the owning repository for every changed contract and behavior.
2. Build a matrix containing repository, responsibility, dependency, base,
   checkpoint, local change contract, and required verification profiles.
3. Order work by public contract dependency. Consumers depend on an immutable
   producer contract or version, never on an uncommitted working tree.
4. Apply each repository's nearest AGENTS.md and project manifest independently.
5. Verify each checkpoint in its own repository. Cross-repository evidence supplements
   rather than replaces local evidence.
6. Review compatibility and rollout at the public boundary. Record an atomic release
   order or an explicit compatibility window when project policy requires one.
7. Report every repository state; do not call the aggregate change complete while one
   required repository remains failed, stale, not run, or blocked.
8. In `improvement-pending`, exchange only the versioned signal, producer disposition,
   immutable-release resolution, and consumer reproduction. Validate every digest
   link and next owner; a transport preserves exact bytes and never reads either
   repository's private lifecycle directory.

## Hard gates

- Do not copy private implementation knowledge across a public contract boundary.
- Do not use sibling filesystem layout as a production dependency.
- Do not silently advance one repository beyond an unresolved owner decision in another.
- Do not let a consumer signal grant producer authority or let producer completion
  substitute for immutable release, consumer adoption, or consumer reproduction.

## Output

Return the repository matrix, ordered checkpoints, contract and rollout decisions,
per-repository evidence, blockers, and next owner.
