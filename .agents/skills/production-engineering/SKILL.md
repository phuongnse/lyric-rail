---
name: production-engineering
description: Apply the small production-engineering invariant floor during planning, implementation, verification, and independent review without replacing semantic judgment with keyword checks.
---

# Production engineering invariants

Assess the [canonical invariant definitions](invariants.json) for every new plan. They
are a small correctness floor, not a design-pattern catalog and not a claim that one
architecture fits every repository.

Keep the core floor at no more than seven invariants. Adding one requires evidence
from a real consumer; first prefer clarifying or consolidating an existing invariant.
This is a design budget, not an assessment or a substitute for evidence.

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
