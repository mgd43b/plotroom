---
title: Keep GitHub health and PR projection coherent in snapshot()
type: bugfix
ticket: 19
created: '2026-11-27'
status: built
baseline_revision: 'c2e8a1bb04d42a1d8d0110450404cbce223d2835'
route: full
route_source: auto
risk: medium
review: thorough
review_source: pinned
lenses_ran: [concurrency, acceptance]
review_loop_iteration: 0
context: [AGENTS.md]
---

<frozen-after-approval reason="human-owned intent">

## Intent

Capture GitHub availability, the PR count and watermark, and the role-filtered board
together for the normal MCP snapshot. Keep poll and merge gate semantics. Verify startup,
empty successful poll, failure, recovery, and a forced commit between capture and render.

</frozen-after-approval>

## Code Map

- The SQLite snapshot already reads the board and GitHub summary in one read transaction.
- The PR poller commits the projection before clearing its in-memory health error.
- The MCP snapshot currently reads the error after awaiting the committed snapshot.

## Tasks & Acceptance

- Commit snapshot health with the PR observation and capture it in the same read transaction
  as the other snapshot fields. Keep GitHub calls and snapshot reads outside the writer queue.
- Pass the captured health into the unchanged snapshot renderer.
- Force a poll to commit after the MCP snapshot capture. Verify the straddling response,
  a subsequent snapshot, empty results, provider failure and recovery, and the unconfigured shape.

## Implementation Notes

Successful polls record health in the same transaction as the PR projection watermark.
MCP reads it alongside the board using the existing SQLite snapshot boundary. Snapshot
health is scoped to the current ledger lifetime, so reopening with an older projection
still reports startup unavailability. Provider failure uses the original public errors
and gate refresh behavior. A health-only commit needs no notification when the controller
is disabled.

## Plan Change Log

## Review Triage Log

Independent concurrency review found that shielding the initial lock wait would let a canceled
waiter enqueue a write later. The fix makes acquisition cancellable and gives the protected
publication task responsibility for releasing the lock. The regression checks no watermark
and no writer notification after the canceled wait. Follow-up concurrency and independent
acceptance inspections found no material issues.

The installed opening review on PR #236 found a second cancellation case and a reader
latency problem with the initial lock. The final change uses committed snapshot health
instead. A deterministic regression holds the writer, queues a poll, reads the prior
snapshot without waiting, then cancels the poll and verifies no watermark was committed.
Another regression checks that an older ledger's health is unavailable after reopening
and that an error-only update does not wake commit waiters.

## Verification

- Built: https://github.com/mgd43b/plotroom/pull/236
- Locked Python 3.14 and 3.15 each passed 69 focused GitHub, snapshot and merge lifecycle
  tests after the canceled-wait fix. The broader check before that refinement included
  merge-controller end-to-end coverage and passed 72 tests per version.
- The final MCP and cancellation regressions and original mounted-PAT smoke passed
  on both locked versions. Ruff lint and formatting, Pyright, and whitespace checks passed.

After rebasing onto the reviewed #234 merge `2345d7ea91c15f3fcf1c33893f47c8dfc113f442`,
the code patches were unchanged. The mounted-PAT smoke, MCP coherence and cancellation
regressions, and adjacent GitHub detail, Commit Status, snapshot, and merge gate checks
passed on both locked Python versions. Each run passed 127 tests, with Pyright and Ruff
clean. Independent review found no new lock ordering or gate interaction on the rebased range.

Final review also covered a newer provider error observed during an earlier commit
and a cancellation after the writer starts. The writer publishes successful health before
notifying its waiters, checks the provider observation revision before clearing errors,
and retains the separate controller refresh. Deterministic tests exercised both races.
On the final revision, 173 targeted tests passed on each locked Python. Pyright, Ruff,
core dependency, and whitespace checks passed. Independent review found no remaining
ordering, cancellation, scope, or gate interaction issue.

Merged forward onto reviewed #238 main `0a60033e3e2807904b943e6c10d81f0699310d19`.
The #235 product patch and canonical story remained unchanged. Both locked Python versions
passed 254 focused tests, including the archived replay regression alongside the snapshot,
mounted-PAT, writer, SQLite and adjacent gate checks. Independent inspection found no
cross-lane interaction. Pyright, Ruff and the core dependency check passed.
