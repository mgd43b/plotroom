---
title: 'Project complete review thread state'
type: feature
ticket: '4'
created: '2026-11-23'
status: built
baseline_revision: '518642b7e89ca29eec8ce4f693f48f85eb966b02'
route: full
route_source: auto
risk: medium
review: quick
review_source: pinned
lenses_ran: [quick]
review_loop_iteration: 0
context: []
---

<frozen-after-approval reason="human-owned intent">

## Intent

Read only a fixed GraphQL query and walk its thread connection to exhaustion. Mark partial or failed observations incomplete and preserve previously known threads.

## Boundaries & Constraints

Use deterministic local fixtures, the existing project repository and credential, and read-only provider operations. Preserve existing databases and successful observations across a restart.

</frozen-after-approval>

## Code Map

- `src/plotroom/adapters/github/read.py`
- `src/plotroom/adapters/github/pr_details.py`
- `src/plotroom/core/github_projection.py`

## Tasks & Acceptance

- [x] Implement through the daemon and existing ledger or WorkItemSource port.
- [x] Verify provider responses and database or mirror state with deterministic fixtures.
- Given a configured project and representative provider fixtures, when the daemon observes the source, then the following holds. Opening and resolving a thread each produce one event, and truncated or broken pagination cannot present a complete thread list.

## Implementation Notes

Read review threads through a paginated GraphQL query. Preserve the last successful list and mark it incomplete on a failed traversal.

## Plan Change Log

## Review Triage Log

Validated rate-limit handling with the original GraphQL response headers, and the second-page failure fixture preserves the prior list with incomplete status.

## Verification

- `uv run python -m pytest -q`
- `uv run ruff check` and `uv run pyright`
