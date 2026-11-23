---
title: "Claude-lane outbox and its drain"
type: feature
ticket: 7
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

**Problem:** Stage Claude-shaped sends and expose a drain-only lease protocol.

**Approach:** Persist the action key across lease expiry and daemon restarts. Authorize lease and completion exclusively for the configured project drain. Wait wakes read-only on ready work. The scripted stand-in exercises delivery locally.

## Boundaries & Constraints

Use the local synthetic inputs in this build, with the committed GitHub projection and existing writer and action contracts. Preserve project isolation and existing rows. Scope is the six assigned stories. Hosting, live client setup, and native drain delivery are outside this implementation.

## I/O & Edge-Case Matrix

| Scenario | Expected behavior |
| --- | --- |
| Concurrent leases or stale completion | A live lease is exclusive, and an expired lease ID cannot complete its successor. |

</frozen-after-approval>

## Code Map

- `core/outbox.py, adapters/store_sqlite/outbox_sql.py, ports/store.py` — Persist pending rows and writer-owned lease generations. Keep stable action keys separate from opaque lease identities.
- `core/ledger.py, adapters/mcp/server.py, app/serve.py` — Expose outbox_lease, outbox_done, and payload-free readiness through wait. Use the existing drain configuration and deny unrelated commands.
- `tests/test_trigger_actions.py, tests/test_transport_contract.py` — Prove expiry, restarts, wake behavior, role restrictions, and delivery through a scripted stand-in. A real native drain delivery remains owner acceptance.

## Tasks & Acceptance

- [x] `core/outbox.py, adapters/store_sqlite/outbox_sql.py, ports/store.py` — Persist pending rows and writer-owned lease generations. Keep stable action keys separate from opaque lease identities.
- [x] `core/ledger.py, adapters/mcp/server.py, app/serve.py` — Expose outbox_lease, outbox_done, and payload-free readiness through wait. Use the existing drain configuration and deny unrelated commands.
- [x] `tests/test_trigger_actions.py, tests/test_transport_contract.py` — Prove expiry, restarts, wake behavior, role restrictions, and delivery through a scripted stand-in. A real native drain delivery remains owner acceptance.

Given a synthetic project daemon, when the relevant input is submitted through the writer or MCP surface, then the behavior in the edge-case matrix is observable in the durable project state.

## Implementation Notes

- Staged Claude-shaped messages in a durable outbox, added drain-only leasing and completion, and exercised the scripted drain through the daemon over HTTP and stdio.

## Plan Change Log

## Review Triage Log

- Independent native review and follow-up. Preserved command receipt replay, restricted Claude routes to configured drains, and verified stale completion, concurrent leases, and an already-blocked wait waking on expiry. Owner acceptance still requires a real native drain delivery.

## Verification

Each drain process supplies its own UUIDv4 for lease idempotency. A retry from that consumer retains its original lease. Distinct consumers can reuse a request ID without sharing a lease. Completion requires the active lease ID.

The local trigger, recovery, action, transport, and migration suite passed all 50 tests on Python 3.14. Ruff, Pyright, and the core dependency check passed. Independent native review confirmed the corrected acceptance paths.

Run `uv run python -m pytest -q` and check that the targeted local acceptance tests pass, including the existing transport contract.
