---
title: 'Deployment status intake'
type: feature
ticket: 11
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

Feed GitHub deployment statuses and same-lane worker reports into the typed inbox.

## Boundaries & Constraints

Use deterministic local fixtures and committed typed facts. External actions, hosted work, and real deployment and drain acceptance stay outside this build.

</frozen-after-approval>

## Code Map

- `src/plotroom/adapters/github/deployments.py` — Read the deployments and status pages with the existing GitHub credential and GET client. Key events by repository, deployment ID, and status ID.
- `src/plotroom/adapters/github/details.py` — Apply deployment reads on the regular detail poll and isolate read errors while preserving rate-limit behavior.
- `src/plotroom/core/reports.py` — Define deploy.status and deploy_report. Require exact environment, SHA, and an allowed state. Deduplicate lane reports on lane, environment, SHA, and state. Refuse archived lanes.
- `src/plotroom/core/catalog.py` — Register the system event and worker-only command using owns_lane.
- `src/plotroom/adapters/mcp/server.py` — Expose deploy_report(lane, environment, sha, state, request_id).
- `tests/test_deploy_status.py` — Exercise GET-only deployment/status reads, poll replay, and own-lane worker report validation on a local fixture.

## Tasks & Acceptance

- [x] `src/plotroom/adapters/github/deployments.py` — Read the deployments and status pages with the existing GitHub credential and GET client. Key events by repository, deployment ID, and status ID.
- [x] `src/plotroom/adapters/github/details.py` — Apply deployment reads on the regular detail poll and isolate read errors while preserving rate-limit behavior.
- [x] `src/plotroom/core/reports.py` — Define deploy.status and deploy_report. Require exact environment, SHA, and an allowed state. Deduplicate lane reports on lane, environment, SHA, and state. Refuse archived lanes.
- [x] `src/plotroom/core/catalog.py` — Register the system event and worker-only command using owns_lane.
- [x] `src/plotroom/adapters/mcp/server.py` — Expose deploy_report(lane, environment, sha, state, request_id).
- [x] `tests/test_deploy_status.py` — Exercise GET-only deployment/status reads, poll replay, and own-lane worker report validation on a local fixture.

Given fixture statuses and an authorized worker report, when both inputs are replayed and a foreign worker tries to report for the lane, then each typed event appears once and the foreign worker receives forbidden.

## Implementation Notes

[Built in PR #154](https://github.com/mgd43b/plotroom/pull/154)

- Both inputs append `deploy.status` facts with exact environment, commit SHA, state, and a derived terminal-state flag. GitHub facts preserve deployment and status IDs. Worker facts carry the reporting lane.
- The regular details poll uses the existing credential and pagination. A status read failure is isolated to its deployment. Rate limits use the details poll's existing cooldown.

## Plan Change Log

## Review Triage Log

- Native review found unbounded historical polling and missing lane attribution. GET polling now revisits the newest deployments and a bounded slice of history with durable list and status cursors. The writer resolves explicit branch refs only when one active lane matches. The fixture checks progress past 30 pages, a new head status on each poll, cooldown, restart, and unbound tag, SHA, and ambiguous refs.

- The review after the main merge found that status pages needed independent event transactions, recently pending deployments needed their own bounded revisit queue, and malformed statuses needed per-record handling. Local fixture regressions cover each case.

- Opening Kodus flagged non-object status entries and transient history cursor resets. Status reads now validate each array member independently and preserve the history cursor on errors. The fixture verifies valid siblings commit and the same historical page retries.

## Verification

`uv run python -m pytest -q tests/test_deploy_status.py` passed all three cases. The tests cover GET-only pagination, failure isolation, cooldown, replay after writer restart, and same-lane ownership, validation, and deduplication over HTTP and the stdio proxy.

The integrated local suites passed 737 tests on each of Python 3.14 and 3.15. Ruff, Pyright for both versions, and the core dependency check passed.
