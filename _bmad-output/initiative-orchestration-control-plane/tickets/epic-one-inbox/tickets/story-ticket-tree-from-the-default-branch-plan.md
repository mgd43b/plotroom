---
title: 'Mirror ready tickets from the default branch'
type: feature
ticket: '10'
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

Read a bounded tree pinned to one immutable commit using the existing credential, and use the trusted BMad ticket reader through the work-item port on each tick.

## Boundaries & Constraints

Use deterministic local fixtures, the existing project repository and credential, and read-only provider operations. Preserve existing databases and successful observations across a restart.

</frozen-after-approval>

## Code Map

- `src/plotroom/adapters/github/ticket_tree.py`
- `src/plotroom/adapters/work_items_bmad.py`
- `src/plotroom/app/serve.py`

## Tasks & Acceptance

- [x] Implement through the daemon and existing ledger or WorkItemSource port.
- [x] Verify provider responses and database or mirror state with deterministic fixtures.
- Given a configured project and representative provider fixtures, when the daemon observes the source, then the following holds. The mirror matches the local reader for the same tree and refreshes after a default-branch push, keeping its last usable state on an error.

## Implementation Notes

Materialize a bounded, pinned Git tree into an isolated temporary directory and use the trusted BMad reader selected by the daemon.

## Plan Change Log

## Review Triage Log

Independent review verified the trust boundary. Fixtures compare local readiness, refresh after a push, and preserve the mirror after a partial tree response.

The opening automated review identified cross-platform path containment risk. The author-controlled input boundary rejects drive-qualified names, and the adapter checks every native destination under the resolved temporary checkout before writing.

## Verification

- `uv run python -m pytest -q`
- `uv run ruff check` and `uv run pyright`
