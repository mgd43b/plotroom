---
title: 'Rule 6 live evaluation and the merge log'
type: feature
ticket: '13'
created: '2026-11-23'
status: in-review
baseline_revision: 'b9bb69382b42edec2a4112113e278dade63a3dd8'
route: full
route_source: auto
risk: high
review: thorough
review_source: pinned
lenses_ran: [native-safety, native-lifecycle, native-acceptance]
review_loop_iteration: 0
context: ['AGENTS.md']
---

<frozen-after-approval reason="human-owned intent">

## Intent

Run the same gate for operator queries and local live evaluation. Log head, verdict and reason transitions durably.

## Boundaries & Constraints

Preserve guard order, the deploy freeze, exact head and main evidence, and durable action safety. Use injected local fixtures for write effects. Keep the clerk's production gate outside the change.

</frozen-after-approval>

## Code Map

- `src/plotroom/core/merge_gate.py` — Share gate evaluation and support an explicit head expectation for dry queries.
- `src/plotroom/core/ledger.py` — Reconcile after relevant writer turns and on provider availability or clock expiry.
- `src/plotroom/core/merge_controller.py` — Persist the most recent verdict signature per PR and log transitions. Supersede the old head.
- `src/plotroom/core/branch_updates.py` — Log a successful branch-update receipt once.

## Tasks & Acceptance

- [x] `src/plotroom/core/merge_gate.py` — Share gate evaluation and support an explicit head expectation for dry queries.
- [x] `src/plotroom/core/ledger.py` — Reconcile after relevant writer turns and on provider availability or clock expiry.
- [x] `src/plotroom/core/merge_controller.py` — Persist the most recent verdict signature per PR and log transitions. Supersede the old head.
- [x] `src/plotroom/core/branch_updates.py` — Log a successful branch-update receipt once.

- Given identical input, when the controller reevaluates, then no duplicate verdict appears. A changed head or decision emits a new pinned event.

## Implementation Notes

- The writer reevaluates after relevant projected facts, policy and lane changes. The merge verdict index points to the latest durable event. A changed head, kind or reason creates a new event, and successful branch update receipts produce BRANCH UPDATED.

## Plan Change Log

## Review Triage Log

- Initial pull discovery could produce refusal items before the first health or check observation. Initial unobserved check evidence is quiet while the dry gate continues to refuse.
- Unreadable configured skip labels initially changed earlier refusal precedence. The gate now preserves early check refusals and handles unreadable skip evidence before allowing a merge.

## Verification

- `uv run python -m pytest -q tests` — acceptance and regression suite passes
