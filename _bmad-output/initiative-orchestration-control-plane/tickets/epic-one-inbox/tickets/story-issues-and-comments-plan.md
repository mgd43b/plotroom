---
title: 'Project issues and verbatim comments'
type: feature
ticket: '5'
created: '2026-11-23'
status: built
baseline_revision: '518642b7e89ca29eec8ce4f693f48f85eb966b02'
route: full
route_source: auto
risk: low
review: quick
review_source: pinned
lenses_ran: [quick]
review_loop_iteration: 0
context: []
---

<frozen-after-approval reason="human-owned intent">

## Intent

Project issue identity, state and assignments plus comment IDs, authors, creation times and verbatim bodies. Deduplicate repeated observations and persist changes in the writer.

## Boundaries & Constraints

Use deterministic local fixtures, the existing project repository and credential, and read-only provider operations. Preserve existing databases and successful observations across a restart.

</frozen-after-approval>

## Code Map

- `src/plotroom/adapters/github/issue_details.py`
- `src/plotroom/core/github_projection.py`

## Tasks & Acceptance

- [x] Implement through the daemon and existing ledger or WorkItemSource port.
- [x] Verify provider responses and database or mirror state with deterministic fixtures.
- Given a configured project and representative provider fixtures, when the daemon observes the source, then the following holds. An issue opening, assignment, comment and closing each produce one event when observed in separate polls, and comment lookup returns the original body.

## Implementation Notes

Read issues with their assignments and maintain comment records by ID. Commit comment transitions and the polling cursor together.

## Plan Change Log

## Review Triage Log

The comment overlap catches equal-timestamp arrivals. Verified two distinct comments with the same timestamp, exact body bytes, one event per observed transition, and no replay after restart.

The opening automated review identified a history bootstrap stall at the page limit. An independent, persisted history cursor now advances in bounded slices alongside recent-comment polling.

## Verification

- `uv run python -m pytest -q`
- `uv run ruff check` and `uv run pyright`
