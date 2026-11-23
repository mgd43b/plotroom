---
title: "Resolve hands-on routing requests"
type: feature
ticket: 5
created: 2026-11-23
status: built
baseline_revision: "c5cbfdca5cabcca23d22ad8e74e33a6b1a297de6"
route: full
route_source: pinned
risk: low
review: thorough
review_source: pinned
lenses_ran: ["blind-hunter", "edge-case-hunter", "verification-gap", "intent-alignment"]
review_loop_iteration: 0
context: ["{project-root}/AGENTS.md"]
---

<frozen-after-approval reason="human-owned intent">

## Intent

The orchestrator needs to find the owning lane for a hands-on action kind and see unassigned kinds in next items. Initial routes come from project configuration and later changes come from the ledger command.

Refs #105.

## Boundaries & Constraints

- Only the orchestrator may set or request routes.
- Seed all configured routes in one writer turn when the entire routes table is empty. A seed may name a future lane.
- Use one state-backed item per currently missing kind. Its acknowledgment snoozes it and configuring a route clears it.

</frozen-after-approval>

## Code Map

- `core/routing.py` owns the typed handlers and startup seed. Use `route_missing:<action_kind>` as the item key.
- `core/catalog.py` and `adapters/mcp/server.py` register `route` and `route_request` with command receipts.
- `app/config.py` parses `[projects.<project>.routes]`, distinct from send-channel settings. `app/serve.py` runs the seed after the writer starts and before runtime inputs.
- `ports/store.py`, `core/unit_of_work.py`, and `adapters/store_sqlite/` wire the project-local routes table and transaction operations. Share migration 0018 with budget state.

## Tasks & Acceptance

**Execution**

- [x] Add table and transaction operations, handlers, commands, and the typed MCP tools.
- [x] Seed the routes once in daemon startup, validate the config, and update transport tests.
- [x] Cover authorization, route lookup, repeated distinct requests, and restart with edited configuration.

**Acceptance Criteria**

- Given an empty ledger and a config seed, when the daemon starts, then a routed kind returns the seeded lane.
- Given a ledger route and an edited config with an override and a new kind, when the daemon restarts, then the ledger route survives and the new kind stays unrouted.
- Given a missing kind and repeated requests including after acknowledgment, when the orchestrator requests it and the worker attempts to set it, then one durable next item exists, and the unauthorized mutation is refused.

## Implementation Notes

- Route assignment and startup seeding close the active missing-route item in the same writer transaction as the route update. A repeated missing request keeps an acknowledged item snoozed and returns its original ID.
- Route values are stored lane IDs, including future lanes. Direct assignments and config seeds use the same label validation.

## Plan Change Log

## Review Triage Log

- Low · Out of scope. A route is a stored assignment to a lane ID. The ledger retains the assignment until an orchestrator updates it, including for future or archived IDs.
- Low · Patch. The transport test now requests a missing route twice and verifies the durable next item through MCP.

## Verification

- `uv run python -m pytest tests/test_routing.py tests/test_transport_contract.py tests/test_config.py` passed 68 tests on Python 3.14.10. Ruff, format, and Pyright checks passed for the touched Python files.
- Combined native review ran four independent lenses. Local verification passed on Python 3.14 and 3.15 with 828 tests each, plus focused migration, delivery, and transport regression checks. Ruff, Pyright, and the core dependency check passed.
