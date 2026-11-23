---
title: 'Next items, ack and snapshot'
type: feature
ticket: 6
created: '2026-11-22'
status: in-progress
baseline_revision: '58f90f7bdc88ea5284ba235793294632d485529d'
route: full
route_source: auto
risk: medium
review: thorough
review_source: pinned
lenses_ran: [native-codex]
review_loop_iteration: 0
context: []
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Persist and rank board items so an orchestrator can rebuild the board and handle pending work.

**Approach:** Register next-item kinds and route item changes and acknowledgments through the existing project writer.

## Boundaries & Constraints

The single local daemon owns writes. Keep project isolation and both local MCP transports. Keep hosted and external workflow work outside this batch.

</frozen-after-approval>

## Code Map

- `src/plotroom/` contains these core entry points or additions — `core/next_items.py, core/unit_of_work.py, core/snapshot.py`.
- Wire them through these repository paths — `adapters/store_sqlite/{items_sql.py,migrations/versions/0003_next_items.py}, adapters/mcp/server.py`.

## Tasks & Acceptance

- [ ] Implement the core behavior and reuse the project writer for durable changes.
- [ ] Wire the normal entry points and verify the acceptance scenario in `tests/test_next_items.py`.

Given ranked fixture items of both kinds, when read, acknowledged twice and rendered with a small budget, then order, transitions, unchanged sequence and omitted counts match the contract.

## Implementation Notes

## Plan Change Log

## Review Triage Log

- Native Codex review. The ack receipt is preserved even when a second acknowledgment has no board effect. The regression reopens the item and confirms that the old request ID still replays its no-op.
- Native Codex review. Next pages preserve rank and expose full open-item detail by ID. Snapshot and next budgets are measured against the indented MCP text envelope.

- Opening Kodus review. Next pages fetch bounded rows from the ranked SQLite view. Item detail uses a separate role-scoped lookup.

## Verification

- `uv run --locked --python 3.14 python -m pytest tests/test_next_items.py`
