---
name: start-change
description: Turn an accepted request into a bounded change contract before planning or implementation begins.
---

# Start a change

Read the owning project specification, relevant repository instructions, and current
behavior. Write a change contract containing the source request, comparison base,
risk, affected projects, observable acceptance criteria, and required verification
profiles. Do not decide unresolved product behavior silently.

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
