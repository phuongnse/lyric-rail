---
name: change-complete
description: Complete an approved change when routed by deliver-change, only while verification and independent review still match the repository snapshot.
---

# Complete a change

Confirm the lifecycle is approved, every required profile passed, every blocking
finding is closed, every non-blocking finding has its required disposition, and the
repository still matches the reviewed snapshot.
Every previously open blocker must have remained visible until the reviewer recorded
its explicit resolution; an omitted finding is not a closed finding.

Run:

    processctl change finish --change-id ID --actor ACTOR --context CONTEXT

The existing `change finish` CLI operation writes one bounded completion receipt and
marks the run completed.
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

Report the readiness capabilities protected or advanced and the remaining planned
gaps after completion. Carry the owner and stable record URL for every accepted-risk
or tracked-follow-up disposition into the durable handoff. Finish never edits
readiness, upgrades a pack version, promotes a capability, or turns `building` into a
production claim; those are reviewed consumer-owned source changes.
