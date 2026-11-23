---
title: "Exactly once across restarts, and branch-glob binding"
type: feature
ticket: 5
created: '2026-11-23'
status: built
baseline_revision: '518642b7e89ca29eec8ce4f693f48f85eb966b02'
route: full
route_source: auto
risk: high
review: quick
review_source: pinned
lenses_ran: [quick]
review_loop_iteration: 0
context: ['{project-root}/AGENTS.md']
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Persist first branch-glob bindings and recover queued actions after a crash.

**Approach:** Accept exactly one PR number or branch glob. Bind each glob to the earliest first-projected matching head branch, before checking merged state. Preserve the binding and firing across restart and replay.

## Boundaries & Constraints

Use the local synthetic inputs in this build, with the committed GitHub projection and existing writer and action contracts. Preserve project isolation and existing rows. Scope is the six assigned stories. Hosting, live client setup, and native drain delivery are outside this implementation.

## I/O & Edge-Case Matrix

| Scenario | Expected behavior |
| --- | --- |
| Two matching PRs or restart between fire and delivery | Later matches never replace a binding. A reopened runtime uses the original pending action key. |

</frozen-after-approval>

## Code Map

- `core/triggers.py, adapters/store_sqlite/triggers_sql.py` — Persist each leaf binding and use the existing complete PR projection. Use stable observation order even when multiple rows arrive in one input.
- `app/runtime.py, core/actions.py` — Use the existing durable action runner contract and atomically apply local effects with the action result.
- `tests/test_triggers.py, tests/test_trigger_recovery.py` — Prove cancellation of the pre-dispatch process, restart and re-delivery with the original key, one action row, and first glob wins.

## Tasks & Acceptance

- [x] `core/triggers.py, adapters/store_sqlite/triggers_sql.py` — Persist each leaf binding and use the existing complete PR projection. Use stable observation order even when multiple rows arrive in one input.
- [x] `app/runtime.py, core/actions.py` — Use the existing durable action runner contract and atomically apply local effects with the action result.
- [x] `tests/test_triggers.py, tests/test_trigger_recovery.py` — Prove cancellation of the pre-dispatch process, restart and re-delivery with the original key, one action row, and first glob wins.

Given a synthetic project daemon, when the relevant input is submitted through the writer or MCP surface, then the behavior in the edge-case matrix is observable in the durable project state.

## Implementation Notes

- Bound each branch-glob leaf to its first observed matching projected head. A subprocess SIGKILL test verifies the startup action loop dispatches the original committed action key exactly once.

## Plan Change Log

## Review Triage Log

- Independent native review and follow-up. Fixed ordering when an existing PR first matches after a rename. Exercised background dispatch after restart and upgrades that preserve old projected PRs and action rows.

## Verification

The local trigger, recovery, action, transport, and migration suite passed all 50 tests on Python 3.14. Ruff, Pyright, and the core dependency check passed. Independent native review confirmed the corrected acceptance paths.

Run `uv run python -m pytest -q` and check that the targeted local acceptance tests pass, including the existing transport contract.
