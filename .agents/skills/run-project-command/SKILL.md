---
name: run-project-command
description: Select, execute, and maintain reproducible project commands for setup, diagnosis, generation, testing, local development, verification, and continuous integration. Use when choosing an executable proof, changing command topology, or handling a blocked project prerequisite.
---

# Run a Project Command

## Goal

Use the smallest project-declared command that proves the required boundary and keep
repeatable execution deterministic.

## Workflow

1. Classify the moment as setup, diagnosis, exploration, implementation proof,
   lifecycle verification, publication, or continuous-integration reproduction.
2. Read `.process/project.json` and the nearest project owner. Run `processctl doctor`
   for the selected environment profile. Reuse current evidence while its checkpoint,
   command, environment, and acceptance boundary remain valid.
3. Select the narrowest declared command. Use a broad profile only for cross-cutting
   invalidation, an inseparable dependency, or an explicit project requirement.
4. Confirm the command is a finite, non-interactive foreground task. Execute declared
   checks through `processctl verify`, or ad-hoc foreground tasks
   through `processctl exec --profile ... -- ...` so verified managed-tool bindings
   and paths are injected. Keep secrets out of arguments and
   evidence. Process-owned success requires both a successful execution boundary and
   warning/error-free admitted stdout and stderr; exit zero cannot override a
   diagnostic failure. Correct the diagnostic at its owner instead of suppressing it
   or substituting a quieter command. Do not run commands concurrently when they
   share mutable build outputs.
5. On a missing prerequisite, use `processctl setup` to inspect the complete plan.
   Apply it only with explicit approval for every declared mutation scope. On failure,
   preserve the exact command, exit status, environment, and missing prerequisite. Do
   not substitute another runtime or evidence boundary.
6. Declare pinned portable tool artifacts with immutable checksums; acquisition,
   verification, safe extraction, atomic installation, and command binding remain
   distribution-owned. Add project-native dependency commands or deterministic checks
   only in the manifest; consumers do not reimplement process machinery.

## Hard gates

- Native read-only inspection is not verification evidence unless declared as such.
- Do not replace focused missing evidence with an unrelated broad suite.
- Do not install tools, change host trust, or mutate external state without authority.
- Treat `readOnly` and command mutation scopes as project-owner attestations, not a
  sandbox. Reject an incomplete scope declaration instead of assuming confinement.
- Run only foreground commands. A declared command must not daemonize, create a
  detached session, or leave background work behind; portable POSIX execution cannot
  contain a process that deliberately escapes its owned process group.
- Keep detached services, interactive shells, log followers, watchers, and stdio
  servers in their project-owned lifecycle. Do not force them through the finite-task
  executor or use them as verification checks.
- An installer exit code is not readiness evidence; the declared probe must pass after
  setup.

## Output

Return moment, selected commands and reasons, results, reused evidence, omitted broad
checks, blockers, and next verification boundary.
