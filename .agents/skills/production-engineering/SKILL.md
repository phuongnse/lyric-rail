---
name: production-engineering
description: Apply intentional design guidance and the small production-engineering invariant floor without replacing contextual judgment with keyword checks.
---

# Production engineering

Assess the [canonical invariant definitions](invariants.json) for every new plan. They
are a small correctness floor, not a design-pattern catalog and not a claim that one
architecture fits every repository.

Keep the core floor at no more than seven invariants. Adding one requires evidence
from a real consumer; first prefer clarifying or consolidating an existing invariant.
This is a design budget, not an assessment or a substitute for evidence.

## Design quality

Use the consumer's architecture, domain model, and accepted design criteria to judge
the affected code. Responsibilities, valid data and state, ownership of rules and
side effects, and contracts between collaborators must be understandable from the
source. A reader should be able to trace a supported behavior and locate its rules
without reconstructing the implementation conversation.

Actively introduce or refine cohesive abstractions when current requirements expose
a hidden responsibility, duplicated policy, or coordination and consistency
obligations leaking into callers. Use consumer-native constructs and reuse sound
existing boundaries. An abstraction earns its place by clarifying behavior or
containing a real change, with its indirection and coupling costs accounted for.
A small caller count does not disqualify an abstraction that clarifies responsibility
or protects a contract.

Preserve clear direct implementations where further separation adds no present
benefit. Judge both the effort to follow a behavior and the reach of a change;
shorter code, more layers, and named patterns do not establish design quality.
Keep refactoring within the accepted scope and avoid unused flexibility. Record
material rationale near the owned code when the structure cannot express it.

For automation identities, use a declared naming convention with clear ownership and
role. The packaged automation-name standard supplies a default that consumers may
override. Consumer bootstrap and configuration code must apply and verify the same
selected convention, including provider-returned names before subsequent side effects.
Provider limits and authenticated identity remain consumer-owned checks; a conforming
name is not evidence of authority. Treat a live rename as an explicit consumer migration.

Assess these outcomes through the existing accepted criteria. They do not add entries
to the canonical invariant assessments or expand an in-flight contract.

## Invariant assessments

At plan time, assess every invariant as `applicable` or `not-applicable`. Explain the
decision from the invariant's trigger. Every applicable assessment names the work
items that will establish its required structure and evidence. `not-applicable` is a
reasoned scope decision, not a waiver for a known violation.

During implementation, follow the required structure for every applicable invariant
and put objective evidence into consumer-owned verification commands. Prefer an
authoritative signal and positive structure. Never approximate open-world meaning
with a keyword, identifier, filename, diagnostic-text, or exception vocabulary.
A literal mapping remains appropriate when an owned, versioned protocol defines the
complete domain, such as a state machine or schema enum.

Verification proves only the deterministic properties exercised by its exact
commands on the unchanged candidate. It must not claim to infer architecture,
security, intent, or arbitrary prose semantics that those commands do not observe.

When an artifact claim depends on byte identity, make encoding and newline rules
explicit at its writer and compare bytes at the exported or installed boundary.
Text-mode newline normalization and a clean Git diff cannot supply that proof;
follow the artifact owner's format rather than imposing one text policy on consumers.

Independent review reassesses every invariant against the contract, plan, complete
diff, profile evidence, and readiness result. Mark it `satisfied`, `not-applicable`,
or `violated`, give a concrete rationale, and cite evidence for `satisfied`. A
violation links to a blocking finding. Approval is impossible while any invariant is
violated.

This floor complements the production-readiness declaration. Immutable packs,
enforced capabilities, consumer-owned profiles, exact-snapshot evidence, and
independent review remain the authority for a production claim.
