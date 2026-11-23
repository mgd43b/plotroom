---
title: "Tracer: a pr_merged trigger notifies the orchestrator once"
type: feature
ticket: 1
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

**Problem:** Implement the first local trigger from committed PR facts.

**Approach:** Once triggers evaluate at the end of a successful writer transaction, including creation. Persist the fire and queued notification together. Use a registered level leaf named pr_merged with an exact PR number in the configured repository. The trigger command is orchestrator-only.

## Boundaries & Constraints

Use the local synthetic inputs in this build, with the committed GitHub projection and existing writer and action contracts. Preserve project isolation and existing rows. Scope is the six assigned stories. Hosting, live client setup, and native drain delivery are outside this implementation.

## I/O & Edge-Case Matrix

| Scenario | Expected behavior |
| --- | --- |
| Projection before or after registration | A merge fires one notification in either order. Worker creation is forbidden. |

</frozen-after-approval>

## Code Map

- `core/triggers.py, core/catalog.py, core/ledger.py` — Register the leaf and command, then evaluate inside the transaction after inputs.
- `adapters/store_sqlite/triggers_sql.py, adapters/store_sqlite/ledger_sql.py, ports/store.py, core/unit_of_work.py` — Persist open and fired statuses. Register core.trigger_fired under the system role and use its event ID to queue exactly one action.
- `tests/test_triggers.py` — Prove both orderings, input bursts, projection sourcing, and writer permissions.

## Tasks & Acceptance

- [x] `core/triggers.py, core/catalog.py, core/ledger.py` — Register the leaf and command, then evaluate inside the transaction after inputs.
- [x] `adapters/store_sqlite/triggers_sql.py, adapters/store_sqlite/ledger_sql.py, ports/store.py, core/unit_of_work.py` — Persist open and fired statuses. Register core.trigger_fired under the system role and use its event ID to queue exactly one action.
- [x] `tests/test_triggers.py` — Prove both orderings, input bursts, projection sourcing, and writer permissions.

Given a synthetic project daemon, when the relevant input is submitted through the writer or MCP surface, then the behavior in the edge-case matrix is observable in the durable project state.

## Implementation Notes

- Exact PR predicates evaluated through the writer after inputs and at registration. Notification effects use the committed action key.

## Plan Change Log

## Review Triage Log

- Independent native review and follow-up. Fixed registration without a configured repository and covered the refusal. Excluded unprojected input events from merge decisions.

## Verification

The local trigger, recovery, action, transport, and migration suite passed all 50 tests on Python 3.14. Ruff, Pyright, and the core dependency check passed. Independent native review confirmed the corrected acceptance paths.

Run `uv run python -m pytest -q` and check that the targeted local acceptance tests pass, including the existing transport contract.
