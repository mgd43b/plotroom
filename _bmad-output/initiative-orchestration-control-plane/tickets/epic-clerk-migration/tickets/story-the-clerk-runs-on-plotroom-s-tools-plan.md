---
title: 'Prepare the clerk pilot'
type: feature
ticket: 3
created: '2026-11-23'
status: built
baseline_revision: '0b0e75fc4c0242ce8202dcc3164dfa0179d9ef0a'
route: full
route_source: auto
review: thorough
review_source: pinned
lenses_ran: [acceptance, edge_cases, verification]
risk: high
context:
  - '{project-root}/AGENTS.md'
---

<frozen-after-approval reason="human-owned intent">

## Intent

Provide an opt-in clerk goal and an offline rehearsal for the owner. The owner retains control of the one-working-day acceptance. No live ledger, credentials, drain, delivery, sweep or merge operations are in scope.

</frozen-after-approval>

## Code Map

- `tests/test_merge_controller_e2e.py` has the in-memory repository and pinned manual merge stand-in. `tests/test_watch.py` has the subprocess watcher pattern.
- Parent provides `plotroom import --config PATH --token-id ID --triggers PATH --ledger PATH` with optional individual files (at least one required), using the private local listener and an orchestrator role. Add fixtures under `tests/fixtures/clerk_import/`; coordinate contents with parent.

## Tasks & Acceptance

- [x] Add `coordination/goals/clerk-plotroom.txt` and a linked procedure. Document offline import → watch → pinned MERGE item → merged observation using the fixture adapter, no real writes.
- [x] Add a fixture rehearsal in `tests/test_clerk_pilot.py`. Exercise CLI import and watch against loopback fixture facts, fresh recheck using both pins, fixture merge, and one trigger fire. Show an outside-gate receipt on a separate PR with no matching offer if feasible. Keep fixture writes in memory.
- [x] Document manual clerk commands, head pin versus main preflight, owner row holds and real schema/evidence prerequisites (#12/#60/#67), named drain, authorized sweep pause, one-day log and cross-check of execution vs observation. Leave owner acceptance pending and the active clerk unchanged.

## Implementation Notes

- [Preparation built in PR #163](https://github.com/mgd43b/plotroom/pull/163). Owner acceptance for the one-working-day pilot remains pending.
- The offline run uses fixture credentials and mock provider reads. A fixture stand-in enforces both pins, and the owner procedure describes which pin the manually executed command enforces.
- Owner acceptance remains pending. The live sweep pause, private copies, named drain, manual command policy and working-day reconciliation are owner steps.

## Plan Change Log

## Review Triage Log

- Medium, acceptance. Starting the watch before reviewing current offers could leave one waiting indefinitely. The goal now has the clerk read all current pages first and retain the first cursor.
- Medium, edge cases. Importing before the sweep pause could duplicate delivery. The procedure now requires the owner to pause before the final copies and reconcile delivery before running the drain.

## Verification

- `uv run --locked python -m pytest -q tests/test_clerk_pilot.py`
- The owner alone verifies the one-day record for #110. #111–113 remain downstream.
- On the reviewed opening tree, 841 local Python 3.14 tests passed. The 40 affected tests passed on Python 3.14 and 3.15.
