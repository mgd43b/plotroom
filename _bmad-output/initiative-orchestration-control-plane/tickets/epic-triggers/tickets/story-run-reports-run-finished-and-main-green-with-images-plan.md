---
title: 'Run reports and main image readiness'
type: feature
ticket: 3
created: '2026-11-23'
status: in-review
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

Add a same-lane worker report for run state and detect current-main CI and image readiness.

## Boundaries & Constraints

Use deterministic local fixtures and committed typed facts. External actions, hosted work, and real deployment and drain acceptance stay outside this build.

</frozen-after-approval>

## Code Map

- `src/plotroom/core/reports.py` — Append one typed run.status fact per lane, run ID, and state.
- `src/plotroom/core/catalog.py` — Register the worker-owned command and internal event. Carry configured image job names into the leaf registry.
- `src/plotroom/adapters/mcp/server.py` — Expose run_report(lane, run_id, state, request_id).
- `src/plotroom/core/triggers.py` — Match terminal run reports by ID and trigger lane when bound. Verify latest completed main verdict and configured job conclusions on the same current tip, including optional SHA pin.
- `src/plotroom/app/serve.py` — Pass the existing GitHub image configuration into predicate construction.
- `tests/test_trigger_predicates.py` — Cover run completion, cross-lane ownership, wrong tip and missing or unsuccessful image jobs.

## Tasks & Acceptance

- [x] `src/plotroom/core/reports.py` — Append one typed run.status fact per lane, run ID, and state.
- [x] `src/plotroom/core/catalog.py` — Register the worker-owned command and internal event. Carry configured image job names into the leaf registry.
- [x] `src/plotroom/adapters/mcp/server.py` — Expose run_report(lane, run_id, state, request_id).
- [x] `src/plotroom/core/triggers.py` — Match terminal run reports by ID and trigger lane when bound. Verify latest completed main verdict and configured job conclusions on the same current tip, including optional SHA pin.
- [x] `src/plotroom/app/serve.py` — Pass the existing GitHub image configuration into predicate construction.
- [x] `tests/test_trigger_predicates.py` — Cover run completion, cross-lane ownership, wrong tip and missing or unsuccessful image jobs.

Given a running lane-owned run and incomplete or mismatched main facts, when the worker reports a terminal state and the current SHA gets a passing CI verdict with every configured image job passing, then the corresponding triggers each fire once.

## Implementation Notes

- Reports use the worker-only lane ownership rule and a state fact deduplicated by lane, run ID, and state. Finished matches the latest report for the ID in the bound lane when supplied. Main readiness reads the latest completed verdict, the current default-branch tip, and every configured image job from that attempt.

## Plan Change Log

## Review Triage Log

- Native review found that an empty image configuration could signal readiness and two lanes could reuse a run identity. Readiness stays false without configured jobs. The writer binds a run ID to the first reporting lane and refuses a report from a second lane. Regressions cover both.

## Verification

Run `uv run python -m pytest -q tests/test_trigger_predicates.py`.
