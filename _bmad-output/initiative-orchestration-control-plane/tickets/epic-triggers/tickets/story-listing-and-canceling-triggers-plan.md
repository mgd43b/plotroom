---
title: "Listing and canceling triggers"
type: feature
ticket: 8
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
context: ['{project-root}/AGENTS.md']
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Expose trigger inventory, cancellation, and board counts.

**Approach:** List stored states and bindings, optionally filtered by lane. Record cancellation through the writer table under the orchestrator role, and read snapshot counts with the board cursor.

## Boundaries & Constraints

Use the local synthetic inputs in this build, with the committed GitHub projection and existing writer and action contracts. Preserve project isolation and existing rows. Scope is the six assigned stories. Hosting, live client setup, and native drain delivery are outside this implementation.

## I/O & Edge-Case Matrix

| Scenario | Expected behavior |
| --- | --- |
| Cancel then merge | No fire or action. Worker and clerk mutation requests are refused. |

</frozen-after-approval>

## Code Map

- `core/triggers.py, core/ledger.py, adapters/mcp/server.py` — Add listing and writer-authorized cancel through established envelopes.
- `core/snapshot.py, adapters/store_sqlite/store.py` — Render counts and preserve the existing board API used by callers.
- `tests/test_triggers.py, tests/test_transport_contract.py` — Prove filtering, all lifecycle states, counts, and denial cases.

## Tasks & Acceptance

- [x] `core/triggers.py, core/ledger.py, adapters/mcp/server.py` — Add listing and writer-authorized cancel through established envelopes.
- [x] `core/snapshot.py, adapters/store_sqlite/store.py` — Render counts and preserve the existing board API used by callers.
- [x] `tests/test_triggers.py, tests/test_transport_contract.py` — Prove filtering, all lifecycle states, counts, and denial cases.

Given a synthetic project daemon, when the relevant input is submitted through the writer or MCP surface, then the behavior in the edge-case matrix is observable in the durable project state.

## Implementation Notes

- Added orchestrator-only inventory and cancellation, optional lane filtering, and board counts in the same SQLite read.

## Plan Change Log

## Review Triage Log

- Independent native review and follow-up. Verified cancellation authorization, replay, and the three lifecycle states with SQLite and MCP coverage.

## Verification

The local trigger, recovery, action, transport, and migration suite passed all 50 tests on Python 3.14. Ruff, Pyright, and the core dependency check passed. Independent native review confirmed the corrected acceptance paths.

Run `uv run python -m pytest -q` and check that the targeted local acceptance tests pass, including the existing transport contract.
