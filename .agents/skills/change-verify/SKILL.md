---
name: change-verify
description: Run the project-owned verification profiles on one unchanged repository snapshot when routed by deliver-change.
---

# Verify a change

## Route card

**State:** `implementing` (or a recorded execution blocker). **Do:** inspect `change
explain`, then execute only the accepted required profiles that are not validly
reusable; use the exact consumer commands and fail closed on scope, mutation, timeout,
or unknown identity. **Evidence:** passed reports bound to one checkpoint and input
identity. **Next:** independent review; profiles are not rerun by review or finish.

Read the registered acceptance criteria and .process/project.json. When publication
is required, commit the complete candidate on a valid publication branch before final
verification. The lifecycle rejects uncommitted candidate changes or an invalid or
empty pinned-base-to-HEAD range before running a profile, and rechecks before review.
Lifecycle run/receipt files remain local. A later commit, even with identical source
content, changes the checkpoint and requires fresh verification and review.

An explicit profile request is an unconditional refresh:

    processctl change verify --change-id ID --profile PROFILE

For a long-running command, add `--progress` to the explicit, `--remaining`, or
`--affected` verification request. The opt-in status stream is written to stderr at a
bounded cadence and reports only the profile/check position, elapsed time, declared
timeout, last successful runner observation, runner responsiveness, and captured byte
count. It must report internal progress as `unknown` when the consumer command exposes
no trusted progress signal; output growth is not treated as proof of test progress.
It never invents percentages, current test names, remaining time, or a passing result.
The stream is operational context, not lifecycle evidence, review approval, or merge
eligibility, and the normal JSON result on stdout remains unchanged.

For a continuation request, inspect the decision first and then execute only
unsatisfied required profiles:

    processctl change explain --change-id ID
    processctl change verify --change-id ID --remaining

`--remaining` reuses only a complete passed whole-profile report whose candidate,
accepted contract and plan, consumer project policy, process authority, runtime and
  bounded child environment all match. It records reuse as a lifecycle event without
  pretending that a command ran again. Missing input identity is unknown and
runs again. Optional configured profiles not selected by the accepted contract are
reported as inapplicable; a required profile missing from the current policy is
blocked. This path never deduplicates check positions or equal check IDs.

A failed full-profile report is not evidence for any required profile. The same run
retains its failed check, one-based position, exit result, timeout/output/stream and
cleanup indicators, bounded stream counts/hashes, safe reproduction arguments, report
digest, recorded time, and candidate checkpoint. `change explain` exposes these facts
through a typed reference with a `current`, `stale`, or `unavailable` label; read its
relative run path for the schema-owned descriptor. It never includes raw stdout,
stderr, traceback, secrets, or a guessed test failure; if the consumer command has no
safe structured failure report, that limit is explicit. Historical or partial reports
are not treated as the current candidate.

When a failed report still matches the exact candidate and input identity,
`--remaining` blocks instead of retrying the same operation. Change the relevant
consumer input, candidate, authority, or dependency through its owner-controlled route,
or deliberately request the explicit full-profile refresh. A later pass proves only
that later execution passed; it does not by itself explain or prove the earlier
failure was fixed. A selective check/module/command run remains diagnostic and never
becomes required evidence.

`change status` exposes bounded run measurements: `remainingBlockedAttempts` counts
non-progress remaining requests, `failedProfileRefreshes` counts explicit full-profile
refreshes after a failed report, `remainingInvalidationExecutions` counts remaining
work launched after a failed report became stale, and `profileExecutions`/`checkLaunches`
count the actual lifecycle work needed by the run. These counters are scenario
evidence, not a latency target or a new telemetry system, and selective diagnostics
outside the lifecycle do not become required evidence.

The stage reuse map is deliberately narrow:

- The accepted contract and registered plan are immutable inputs reused from start
  through finish by their recorded digests.
- A valid whole-profile report may be reused during the same implementation cycle;
  the remaining-work coordinator batches adjacent reuse decisions and records the
  original evidence timestamp and input identity.
- Review and finish consume those same verified reports after checking the current
  checkpoint; they do not rerun the profiles. A review assignment may be resumed only
  for the same accepted change.
- A new implementation/correction cycle clears verification. A changed candidate,
  policy, authority, runtime, comparison base or unknown input requires execution.

This is operation-scoped reuse, not a persistent success cache. Do not carry a
calculation or report across a possible child mutation/concurrency boundary unless
the lifecycle takes the existing fresh snapshot and identity checks again.

For fast feedback based on the changed areas, use the consumer's explicit impact
policy:

    processctl change explain --change-id ID --impact
    processctl change verify --change-id ID --affected --affected-profile development

The policy lives in the consumer project configuration as one current version-1
`impactProfiles` definition. It declares independently executable units, exact
argument-array commands, and normalized path patterns for feedback. It may additionally
name required `finalProfiles`; those profiles explicitly assert that their selected
units are complete final assurance, and each must contain an explicit `scope: "global"`
unit whose paths include the universal `**` pattern for cross-cutting reach. A unit with
`scope: "global"` is a deliberate global rule only when that universal pattern is
declared; narrower patterns never cover unrelated paths. The process computes paths
from the pinned
comparison base through the current candidate and selects every matching unit in
declared order.

Affected execution remains feedback-only. When the current policy declares a final
profile, `change verify --remaining` executes the resolved units as
`impact-assurance` evidence, records the selection identity in the lifecycle report
and receipt, and lets review/finish consume that evidence. An unresolved final
selection blocks remaining verification; it never silently falls back to a partial
pass. `change verify --profile PROFILE` remains the explicit full-profile refresh.

Impact selection is fail-closed. A missing policy, unsupported policy, changed path
with no matching unit, or invalid unit returns `unresolved`/`unavailable` with the
paths and the action `inspect-diff-and-update-impact-policy`; it launches no command
and never falls back to a full profile. The agent must inspect the diff and
dependency reach, update the consumer-owned mapping or obtain an owner decision,
then rerun `change explain --impact`. Full verification is an explicit final
boundary for non-opted-in profiles, not a recovery path for unresolved final impact.

For process adoption changes, use `--remaining` with `processctl change explain` to
satisfy required profiles proportionally. First verify adoption integrity
(`processctl adoption check`, hash lock, doctor). When consumer product sources are
unchanged and prior passing profile reports match the current candidate and environment,
`--remaining` reuses valid reports without rerunning unaffected checks, and executes
any unsatisfied profiles. The current impact policy, final impact assurance, and whole-profile
reuse are separate mechanisms: an unresolved feedback selection blocks affected
execution, an unresolved final selection blocks remaining verification, and an owner
or release workflow may intentionally request the explicit full profile.

Commands are exact argument arrays with timeouts. Do not substitute a different tool
or narrower check when a required command fails. A non-zero exit is a command failure;
timeout, output limit, stream, cleanup, spawn, and other inability-to-produce-report
conditions are execution failures. The report or lifecycle blocker identifies which
condition occurred and the missing consumer action. A command failure report leaves
the change implementing but blocks same-input remaining work; a spawn failure records
the same bounded stop without raw process detail. Successfully cleaned post-exit
descendants remain recorded without replacing the foreground command result.

In a fresh session, use the consumer's declared bootstrap and the supported runtime
that has the process and required project dependencies installed. Invoke the
installed process entry point through that runtime; do not assume a source
`processctl.py` or a particular virtual-environment directory exists in every consumer.
The bounded runner prepends the invoking interpreter's directory to child `PATH`
while preserving the declared command arrays, explicit executable paths, and the
remaining caller path. Missing executables remain bounded execution failures.
Select or install dependencies only through consumer-owned setup commands; this
route does not provide an implicit installer.

A failed report may include a safe selective-reproduction argument array. It can
confirm the isolated failure through the same bounded runner, but it remains a
diagnostic action: never submit it in place of the required full profile, and do not
expect it to reveal captured stdout or stderr.

For document checks, use the same selected standard and source records that produced
the artifact. The artifact commands report its effective standard/version/digest;
consumer-owned profiles retain those results within ordinary verification evidence.
Overrides must remain in snapshot-covered consumer files. These checks validate the
declared format or generated bytes, not the truth of arbitrary prose or lifecycle
approval. Entirely custom formats use consumer-owned template and validator commands.

The contract must already include conditional profiles required by affected enforced
capabilities. `--remaining` may select only the accepted contract's requiredProfiles;
it does not infer reuse from branch names, labels, filenames, commands or diagnostics.
The separate `--affected` path uses only the explicit current `impactProfiles`
policy; it does not guess missing coverage from filenames. do not run every planned
production gate for an unrelated change, and do not treat a passing baseline profile
as evidence for a planned capability whose gap remains open. A readiness promotion
is valid only when all evidence named by that capability passes on this same snapshot.

When all required profiles pass on the same snapshot, the lifecycle becomes verified;
route to **change-review**.

For a publication-enabled consumer, treat source or PR metadata as a separate
evidence boundary. A code candidate change must produce fresh code evidence; a
title/body-only PR edit may reuse code evidence only while the exact head and all
code inputs remain unchanged, and must run the consumer's publication check against
the current base/head/title/body. For this consumer, retained code evidence is an
immutable versioned artifact linked by the provider to a successful `ci.yml`
workflow run; its exact artifact protocol name plus provider run metadata binds the
base/head/branch/matrix record. A skipped,
missing, expired, cancelled, failed, stale or out-of-order provider record is not a
pass. If the event wiring cannot show which current candidate and metadata a check
evaluated, leave the PR blocked and report that unknown rather than refreshing an
unrelated heavy profile.
