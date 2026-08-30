---
name: verify-change
description: Run the project-owned verification profiles and bind their results to one unchanged repository snapshot.
---

# Verify a change

Read the registered acceptance criteria and .process/project.json. Run every required
profile through the lifecycle:

    processctl change verify --change-id ID --profile PROFILE

Commands are exact argument arrays with timeouts. Do not substitute a different tool
or narrower check when a required command fails. A command failure, timeout,
surviving descendant, or tracked repository mutation is a failure and leaves the
change in implementing.

When all required profiles pass on the same snapshot, the lifecycle becomes verified;
route to **review-change**.
