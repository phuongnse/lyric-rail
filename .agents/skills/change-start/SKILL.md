---
name: change-start
description: Turn an accepted request into a bounded change contract when deliver-change routes a new change to lifecycle start.
---

# Start a change

Read the owning project specification, relevant repository instructions, and current
behavior. Write a change contract containing the source request, comparison base,
risk, affected projects, observable acceptance criteria, and required verification
profiles. Do not decide unresolved product behavior silently.

When the work produces an issue record, PR description, release notes or an automation name, inspect the consumer's
selected artifact standard and existing publication checks. The consumer may override
the packaged defaults. Keep document-format choices separate from lifecycle approval.

Choose the consumer's comparison ref deliberately. Start resolves it once to an
existing commit and returns `comparisonBaseCommit`; use that recorded commit for
later diff/review work. The accepted contract and its digest retain the original ref.

When the accepted change adds or materially changes logic, state, or collaboration
boundaries, read the design quality guidance in **production-engineering**. Express
applicable consumer design standards as observable outcomes in the existing
acceptance criteria: identify the responsibility or contract that must be clear and
the supported behavior it must make understandable. Keep those outcomes within the
accepted behavior and affected code; routine edits need no separate design exercise.

Read the readiness result from `processctl project validate --json`. Use the accepted
request, consumer rules, and inspected behavior to identify only capabilities this
change affects or explicitly advances. State that relationship in the summary and
observable acceptance outcomes. Include the evidence profiles of every affected
enforced capability, including conditional security/release profiles required by the
consumer. Unrelated planned gaps remain visible and non-blocking. A planned capability
may become change scope only through an explicit owner-accepted outcome.

For a process change, include consumerEvidence that names the real consumer and
incident or request. When the project configures an accepted issue URL prefix, source
must be that exact prefix plus a canonical positive issue number; placeholders,
cross-repository URLs, queries, and fragments fail before state is written. This
local shape check does not replace owner acceptance. Validate and register the
contract:

    processctl contract validate --kind change change.json
    processctl change start --actor ACTOR --context CONTEXT --contract change.json

Do not edit implementation before the lifecycle reports specified.

If `.process/project.json` opts in with `lifecycle.publication.required: true`, start
also runs the existing read-only publication branch validator against the current
checkout branch before creating `.process/runs/ID`. A rejected branch leaves no new
run state. The branch convention remains consumer-owned; the opt-in only makes the
consumer's existing publication compatibility rule a lifecycle preflight.
