---
name: process-improve
description: Change the shared process only in response to evidence from a real consumer and prefer removing complexity over adding governance.
---

# Improve the process

Before opening a process change, identify the consumer repository and the concrete
incident, failed adoption, repeated friction, or missing capability. Put that evidence
in the normal change contract. A hypothetical self-governance concern is not enough.

A planned readiness gap can supply evidence only when a real consumer attempt exposes
missing shared guidance or enforcement. Fix consumer-specific behavior in the
consumer. Promote only the reusable invariant, and publish changed pack requirements
under a new immutable pack version; never mutate a version already selected by a
consumer or make process adoption depend on upgrading that pack.

## Consumer issue handoff

From a consumer-only checkout, first fix or safely block the current consumer change
under its existing authority. Then classify the remaining problem. Product behavior
stays in the consumer; only a confirmed shared guidance, enforcement, adoption, or
capability gap is a process-improvement candidate.

Prepare a bounded, sanitized issue draft under the active run, for example
`.process/runs/CHANGE_ID/process-improvement-issue.md`. Include the consumer identity
or safe pseudonym, exact process and pack versions, incident type, observed behavior,
expected invariant, check/finding/digest or minimal reproduction, current mitigation,
why the invariant is reusable, and whether the consumer can continue. Never include
secrets, credentials, tokens, media, private source, raw private logs, or
production-only metadata. Use a public summary that links to private evidence only
when authorized readers can access it.

Use a stable title key such as
`[consumer-process][CONSUMER][PROCESS-VERSION][INVARIANT]`. Search before creating:

    gh issue list --repo phuongnse/engineering-process --state open \
      --search 'STABLE-KEY in:title'

If an issue already owns the invariant, link it in the consumer report; add a
sanitized comment only with owner authorization. Otherwise present the complete draft
to the owner. Open it only after explicit authorization:

    gh issue create --repo phuongnse/engineering-process \
      --title 'STABLE-KEY concise summary' \
      --body-file .process/runs/CHANGE_ID/process-improvement-issue.md

When this handoff came from a pending schema-version 7 review, return the existing or
newly owner-authorized issue's stable HTTPS URL to that review. It remains
`review-pending`, and a `shared-process` disposition cannot submit without that
`recordUrl`.

Do not run issue creation from consumer CI, reuse a consumer or Renovate write token,
or treat missing GitHub access as a blocker. Without authorized `gh` access, return
the draft, the open-issue search URL containing the complete stable key, and then the
`Consumer process improvement` issue-form URL. The owner must search and reuse an
existing issue before manual submission, copy the same stable key into the editable
issue title, and authorize any comment. A generic form title is not a deduplication
key.
An issue is asynchronous evidence, not permission to change the process or a reason
to wait for a new release.

Find the smallest reusable correction. Prefer, in order:

1. delete an obsolete rule or surface;
2. clarify a skill route;
3. repair an existing deterministic check;
4. add a new gate only when the consumer evidence proves the other options cannot
   protect the required invariant.

Use **deliver-change** for the actual work and require the same independent final review
as any consumer change. Track cross-repository discussion in ordinary issues or pull
requests; do not create a second lifecycle or evidence federation. The process never
self-publishes or self-merges: the owner retains release and adoption authority, and
the next consumer result becomes evidence for another bounded iteration.

When maintainers accept the issue, use its URL as the process change `source` and copy
its bounded incident into `consumerEvidence`. Close the issue only after the process
release is adopted and the originating consumer confirms the incident no longer
reproduces; a merged producer change alone is not consumer validation.
