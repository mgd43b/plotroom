---
title: 'Project PR labels, actors and changed paths'
type: feature
ticket: '15'
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

Read the label timeline and current label list, preserve old and new paths on renames, and resolve lanes from same-repository PR branches at commit. Reuse the existing lane-ready guard.

## Boundaries & Constraints

Use deterministic local fixtures, the existing project repository and credential, and read-only provider operations. Preserve existing databases and successful observations across a restart.

</frozen-after-approval>

## Code Map

- `src/plotroom/adapters/github/pr_details.py`
- `src/plotroom/core/github.py`
- `src/plotroom/core/github_projection.py`

## Tasks & Acceptance

- [x] Implement through the daemon and existing ledger or WorkItemSource port.
- [x] Verify provider responses and database or mirror state with deterministic fixtures.
- Given a configured project and representative provider fixtures, when the daemon observes the source, then the following holds. A labeled PR and a renamed file produce durable observations with label actor and previous path, and a stale lane-ready head conflicts.

## Implementation Notes

Read label history and current labels, record file renames on the observed head, and reuse the existing lane matching and lane_ready checks.

## Plan Change Log

## Review Triage Log

Guarded file results against a concurrent head change and invalidate files when a newly projected head supersedes them. Fixtures verify actor, rename origin, lane attribution, and stale READY rejection.

## Verification

- `uv run python -m pytest -q`
- `uv run ruff check` and `uv run pyright`
