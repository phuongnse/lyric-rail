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

The contract must already include conditional profiles required by affected enforced
capabilities. Run those profiles exactly; do not run every planned production gate for
an unrelated change, and do not treat a passing baseline profile as evidence for a
planned capability whose gap remains open. A readiness promotion is valid only when
all evidence named by that capability passes on this same snapshot.

When all required profiles pass on the same snapshot, the lifecycle becomes verified;
route to **review-change**.
