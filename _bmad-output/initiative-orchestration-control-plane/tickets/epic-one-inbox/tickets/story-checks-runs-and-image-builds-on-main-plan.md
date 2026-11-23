---
title: 'Project check states and default-branch workflow verdicts'
type: feature
ticket: '3'
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

GitHub check runs, workflow attempts, and jobs are polled through the configured repository and credential. Persist state transitions atomically with the latest data.

## Boundaries & Constraints

Use deterministic local fixtures, the existing project repository and credential, and read-only provider operations. Preserve existing databases and successful observations across a restart.

</frozen-after-approval>

## Code Map

- `src/plotroom/adapters/github/read.py`
- `src/plotroom/adapters/github/ci.py`
- `src/plotroom/core/github_projection.py`

## Tasks & Acceptance

- [x] Implement through the daemon and existing ledger or WorkItemSource port.
- [x] Verify provider responses and database or mirror state with deterministic fixtures.
- Given a configured project and representative provider fixtures, when the daemon observes the source, then the following holds. A repeated observation is quiet, a changed check status or new check run emits a fact, and a pending run preserves the latest completed verdict.

## Implementation Notes

Poll the default branch and selected PR heads through the daemon. Preserve recent workflow attempts and their job results across reruns.

## Plan Change Log

## Review Triage Log

Shared-SHA attribution uses assignments from check-run payloads and the current PR projection. Tests cover state transitions, reruns, and the prior completed verdict while a rerun runs.

## Verification

- `uv run python -m pytest -q`
- `uv run ruff check` and `uv run pyright`
