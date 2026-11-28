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

- Serialize capture against the PR observation commit and provider health changes without
  serializing GitHub network requests or turning snapshots into writer transactions.
- Pass the captured health into the unchanged snapshot renderer.
- Force a poll to commit after the MCP snapshot capture. Verify the straddling response,
  a subsequent snapshot, empty results, provider failure and recovery, and the unconfigured shape.

## Implementation Notes

The observation lock covers the existing SQLite snapshot read and in-memory health together.
Successful poll publication holds it across the existing writer commit and the existing
merge-controller health refresh. Availability still uses the original public error strings.
Provider calls stay outside the lock. Snapshot reads remain outside the writer queue.

## Plan Change Log

## Review Triage Log

Independent concurrency review found that shielding the initial lock wait would let a canceled
waiter enqueue a write later. The fix makes acquisition cancellable and gives the protected
publication task responsibility for releasing the lock. The regression checks no watermark
and no writer notification after the canceled wait. Follow-up concurrency and independent
acceptance inspections found no material issues.

## Verification

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
