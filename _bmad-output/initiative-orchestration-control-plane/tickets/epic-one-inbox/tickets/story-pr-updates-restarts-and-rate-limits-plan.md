---
title: Project PR updates and resume GitHub polling
type: feature
ticket: 14
created: '2026-11-23'
status: built
baseline_revision: 1749cb7171f60fd14e15bd02d4fb30427d126fa6
route: full
route_source: auto
risk: medium
review: thorough
review_source: pinned
lenses_ran: []
review_loop_iteration: 0
context: []
---

<frozen-after-approval reason="human-owned intent">

## Intent

Preserve each observed PR head and lifecycle transition once, keep per-PR freshness and completeness, and resume a durable fetcher cursor across interrupted polls and rate limits.

## Boundaries & Constraints

Keep one core projection for lane PR state. Derive GitHub-backed lane heads from that projection. A failed page, rate limit, or interrupted transaction preserves the cursor and prior complete projection. Follow GitHub's retry timing using the supplied clock.

</frozen-after-approval>

## Code Map

The pull request fetcher stages a complete paginated response. The project writer processes its observations against the last cursor and saves events, rows, and cursor together.

## Tasks & Acceptance

- [x] Record heads, close and reopen with distinct fact versions.
- [x] Track PR freshness and check GitHub-backed lane readiness against the projection.
- [x] Verify rate limits, interrupted pagination, and restart locally.

## Implementation Notes

The loopback fixture exercised head changes and close/reopen cycles, a SIGKILL between pages, and recovery after rate limits. Incremental reads include a five-minute overlapping window and periodically reconcile the complete list.

## Plan Change Log

## Review Triage Log

The independent native Codex review inspected the combined batch.

- P2, verified and corrected. Bound paginated reads with the committed cursor, using an inclusive cutoff and periodic reconciliation.
- P2, verified and corrected. Reuse the projection’s unique branch match for lane readiness.
- P2, verified and corrected. Persist provider retry deadlines separately from the projection cursor.

## Verification

- uv run python -m pytest tests/test_github.py tests/test_lanes.py tests/test_migrations.py
