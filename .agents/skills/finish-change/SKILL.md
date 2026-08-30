---
name: finish-change
description: Complete an approved change only while its verification and independent review still match the current repository snapshot.
---

# Finish a change

Confirm the lifecycle is approved, every required profile passed, every blocking
finding is closed, and the repository still matches the reviewed snapshot. Then run:

    processctl change finish --change-id ID --actor ACTOR --context CONTEXT

The command writes one bounded completion receipt and marks the run completed.
Completion does not itself grant merge, deployment, or release authority; those
remain project-owned operations. Never report completion from prose alone.
