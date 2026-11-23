---
title: 'Trigger refactor sweep'
type: refactor
ticket: 9
created: '2026-11-23'
status: built
baseline_revision: '3ecf20da2ba64a6e5e2e18f4e41b50db05b3fab0'
route: full
route_source: auto
risk: low
review: thorough
review_source: pinned
lenses_ran: [native]
review_loop_iteration: 0
context: []
---

<frozen-after-approval reason="human-owned intent">

## Intent

Resolve deferred local trigger findings or route them to the ticket tree.

## Boundaries & Constraints

Use deterministic local fixtures and committed typed facts. External actions, hosted work, and real deployment and drain acceptance stay outside this build.

</frozen-after-approval>

## Code Map

- `src/plotroom/core/triggers.py` — Share PR selection and predicate evaluation across leaves and firing modes without altering the existing once command contract.
- `tests/test_triggers.py and tests/test_trigger_recovery.py` — Verify existing binding, composition, cancellation and recovery expectations after consolidation.

## Tasks & Acceptance

- [x] `src/plotroom/core/triggers.py` — Share PR selection and predicate evaluation across leaves and firing modes without altering the existing once command contract.
- [x] `tests/test_triggers.py and tests/test_trigger_recovery.py` — Verify existing binding, composition, cancellation and recovery expectations after consolidation.

Given the six prior Epic 3 plan logs, when each deferred finding is checked against current source, then local cleanup passes CI and outstanding owner evidence stays on its existing ticket.

## Implementation Notes

- Checked the prior six Epic 3 plan logs and resolved #148 review threads. Reused PR selection across leaves and generalized the trigger state transition in one place. No deferred local item remained from those reviews. Real native drain acceptance remains on #60.

## Plan Change Log

## Review Triage Log

- Triaged all six initial native review findings in the deployment, run, and recurring-trigger plans. Each is addressed in the combined lane.

- The follow-up native review on the integrated main base identified the existing GitHub issue-list pagination cap. The ticketer acknowledged it for Inbox triage. New trigger and deployment findings are covered by focused regression tests.

## Verification

Run `uv run python -m pytest -q tests/test_triggers.py tests/test_trigger_recovery.py`.

The integrated local suites passed 737 tests on each of Python 3.14 and 3.15. Ruff, Pyright for both versions, and the core dependency check passed.
