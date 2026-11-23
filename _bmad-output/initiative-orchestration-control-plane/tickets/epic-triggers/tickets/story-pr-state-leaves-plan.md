---
title: 'GitHub state predicates'
type: feature
ticket: 2
created: '2026-11-23'
status: built
baseline_revision: '3ecf20da2ba64a6e5e2e18f4e41b50db05b3fab0'
route: full
route_source: auto
risk: medium
review: thorough
review_source: pinned
lenses_ran: [native]
review_loop_iteration: 0
context: []
---

<frozen-after-approval reason="human-owned intent">

## Intent

Add PR check, review-thread, and issue closure predicates backed by the committed GitHub projection.

## Boundaries & Constraints

Use deterministic local fixtures and committed typed facts. External actions, hosted work, and real deployment and drain acceptance stay outside this build.

</frozen-after-approval>

## Code Map

- `src/plotroom/core/triggers.py` — Reuse exact PR number and first matching branch binding for all PR leaves. Require complete records and latest successful current-head checks. Read complete thread and issue resources.
- `tests/test_trigger_predicates.py` — Exercise current-head success, rejected incomplete facts, review-thread resolution, reopened issues, and shared branch binding.

## Tasks & Acceptance

- [x] `src/plotroom/core/triggers.py` — Reuse exact PR number and first matching branch binding for all PR leaves. Require complete records and latest successful current-head checks. Read complete thread and issue resources.
- [x] `tests/test_trigger_predicates.py` — Exercise current-head success, rejected incomplete facts, review-thread resolution, reopened issues, and shared branch binding.

Given incomplete, stale, or nonmatching facts, when complete current projected facts meet each clause, then only that clause fires.

## Implementation Notes

- All PR leaves share the first-match branch binding code. Green requires a nonempty, complete check view for the current PR head and success for the latest run of each check name. Threads and issues require complete committed resources.

## Plan Change Log

## Review Triage Log

- The review after the main merge found stale review snapshots and check-name collisions across apps. Thread evidence now matches the current PR head and version. Check attempts use the same app-aware grouping as the merge gate. The existing repository-wide issue projection page limit was queued by the ticketer for Inbox triage. The trigger requires complete issue evidence.

## Verification

Run `uv run python -m pytest -q tests/test_trigger_predicates.py`.

The integrated local suites passed 737 tests on each of Python 3.14 and 3.15. Ruff, Pyright for both versions, and the core dependency check passed.
