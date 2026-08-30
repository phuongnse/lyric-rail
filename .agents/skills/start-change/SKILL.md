---
name: start-change
description: Turn an accepted request into a bounded change contract before planning or implementation begins.
---

# Start a change

Read the owning project specification, relevant repository instructions, and current
behavior. Write a change contract containing the source request, comparison base,
risk, affected projects, observable acceptance criteria, and required verification
profiles. Do not decide unresolved product behavior silently.

For a process change, include consumerEvidence that names the real consumer and
incident or request. Validate and register the contract:

    processctl contract validate --kind change change.json
    processctl change start --actor ACTOR --context CONTEXT --contract change.json

Do not edit implementation before the lifecycle reports specified.
