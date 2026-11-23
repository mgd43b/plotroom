---
title: 'Mirror ready tickets from GitHub Issues'
type: feature
ticket: '16'
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

Read milestones, issue labels, sub-issues and blockers, and classify the one configured source according to BMad’s GitHub store mapping.

## Boundaries & Constraints

Use deterministic local fixtures, the existing project repository and credential, and read-only provider operations. Preserve existing databases and successful observations across a restart.

</frozen-after-approval>

## Code Map

- `src/plotroom/adapters/github/issue_details.py`
- `src/plotroom/adapters/work_items_github_issues.py`
- `src/plotroom/app/config.py`
- `src/plotroom/app/serve.py`

## Tasks & Acceptance

- [x] Implement through the daemon and existing ledger or WorkItemSource port.
- [x] Verify provider responses and database or mirror state with deterministic fixtures.
- Given a configured project and representative provider fixtures, when the daemon observes the source, then the following holds. A blocker in review satisfies an issue dependency, unresolved blockers stay blocked, and the mirror accepts a single source per project.

## Implementation Notes

Read structural GitHub issue data into the BMad work-item source. Apply the starter and local store mapping, including refinement state from BMad ticket metadata.

## Plan Change Log

## Review Triage Log

Corrected child order, refinement parity, and cached metadata. A bounded relationship scan preserves the last good mirror until initial relationships are complete. Fixtures cover classification, CLI parity, and the runtime tick.

## Verification

- `uv run python -m pytest -q`
- `uv run ruff check` and `uv run pyright`
