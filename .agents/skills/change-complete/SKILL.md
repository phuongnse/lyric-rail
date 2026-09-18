---
name: change-complete
description: Complete an approved change when routed by deliver-change, only while verification and independent review still match the repository snapshot.
---

# Complete a change

## Route card

**State:** `approved`. **Do:** recheck the exact reviewed checkpoint and let
`change finish` write the one receipt; keep release, merge, adoption, and deployment
with the consumer owner. **Evidence:** required profiles, approved independent
review, current publication boundary, incident-intake result, and receipt. **Next:**
owner-controlled publication/adoption, or a new implementation cycle if the candidate
changes.

Confirm the lifecycle is approved, every required profile passed, every blocking
finding is closed, every non-blocking finding has its required disposition, and the
repository still matches the reviewed snapshot.
Every previously open blocker must have remained visible until the reviewer recorded
its explicit resolution; an omitted finding is not a closed finding.

Run:

    processctl change finish --change-id ID --actor ACTOR --context CONTEXT

The existing `change finish` CLI operation writes one bounded completion receipt and
marks the run completed.

For an opted-in project, `change finish` first runs the existing read-only publication
validators against the current branch, HEAD commit subject, and the recorded
`comparisonBaseCommit` through the exact current HEAD. It records the validated
branch, subject, and pinned range in the single current version-1 receipt only after
the repository and branch remain unchanged. A failed preflight leaves the approved
run incomplete.

Before writing the receipt, `change finish` executes the automated incident intake preflight: it inspects lifecycle history and verification evidence for the closed taxonomy, records the complete stable-key decision, and performs tracker I/O only under the consumer's process-change policy. Search/writer failures, suppression, reuse, and creation are recorded as bounded intake events; a recorded result is not retried and intake never blocks consumer completion.

This source preflight does not validate a provider pull-request body or make a pull
request ready. The consumer's required CI must run the same branch, head-commit,
range, and rendered-body checks against the exact pull-request head before readiness;
merge, publication, deployment, and release authority remain consumer-owned.

Before marking a PR ready, render its body from the selected standard and actual
completion evidence. Run the consumer's publication preflight, including the current
branch, exact head subject, pinned base-to-head range, and ready-state body. For a
consumer using the packaged publication checks, all four commands must pass:

    processctl publication validate-branch --branch BRANCH
    processctl publication validate-commit --subject HEAD_SUBJECT
    processctl publication validate-range --branch BRANCH --range BASE_SHA..HEAD_SHA
    processctl publication validate-pr --title TITLE --branch BRANCH --state ready --body-file BODY_PATH

Use actual current PR metadata and the selected consumer root. Keep the PR draft
when a check fails, and repeat the same checks in required CI/branch protection.
Custom publication policy uses consumer-owned commands at the same boundary.

For the first complete PR, prepare the one body/title/branch/base/head candidate
before calling the provider, then validate that same candidate before `gh pr create`
or the equivalent API. For the packaged standard, the normal sequence is
`artifact prepare-pr-data`, render the selected body, the four publication validators
above, and only then provider creation. An existing PR is edited only when the
current title/body actually differs; metadata repair is not a CI-refresh workaround.
Keep a draft when evidence or metadata is incomplete. A successful publication
check, auto-merge request, or merge request is not itself a merge result.

Completion does not itself grant merge, deployment, or release authority; those
remain project-owned operations. Never report completion from prose alone.

The author/coordinator maintains the PR description: fill known contract facts early,
then use actual verification, independent-review and receipt results. A bot-created
draft is an intake artifact. Before ready/merge, replace unresolved placeholders and
run the consumer's selected document check. The independent reviewer owns the verdict;
the user is not expected to fill the automation's placeholders manually.

When the accepted work includes a release, make its contents reviewable: what ships,
user impact, source issues or changes, and required upgrade/compatibility actions.
Use the consumer's reviewed release records as the authority. The consumer owns
format, tooling and publication decisions; a list of internal execution events or
version-bump PR titles does not explain shipped behavior.
Use the selected release-note standard for both generation and verification against
those records. Consumer overrides do not replace lifecycle evidence or release authority.

Use a normal trailing `Refs ISSUE.` line for producer and intermediate pull requests.
Only a contract-identified final consumer adoption that has verified the released
behavior may use `Closes ISSUE, closes ISSUE.` after the completed public checklist.
Repeat the full keyword and local or `OWNER/REPOSITORY#NUMBER` reference for every
issue; never close source issues merely because the producer release merged.

When the consumer selects an issue artifact standard, render and validate its closed
record from actual resolution, implementation, verification, and applicable
release/adoption/consumer-confirmation evidence before the external tracker is closed.
The artifact result does not itself change provider state or prove arbitrary prose.

Report the readiness capabilities protected or advanced and the remaining planned
gaps after completion. Carry the owner and stable record URL for every accepted-risk
or tracked-follow-up disposition into the durable handoff. Finish never edits
readiness, upgrades a pack version, promotes a capability, or turns `building` into a
production claim; those are reviewed consumer-owned source changes.

Completion consumes only the current run's contract, plan, fresh verification, and
approved review. A superseding run's prior relation is historical provenance; it does
not make the prior run's evidence, approval, findings, or receipt valid for the new
candidate. The prior blocked run remains visible and is not rewritten or marked
complete by finishing its replacement.
