---
title: 'Workers and lanes'
type: feature
ticket: 8
created: '2026-11-22'
status: in-progress
baseline_revision: '58f90f7bdc88ea5284ba235793294632d485529d'
route: full
route_source: auto
risk: medium
review: thorough
review_source: pinned
lenses_ran: []
review_loop_iteration: 0
context: []
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** The orchestrator needs to track configured workers and recover their lanes after restart.

**Approach:** Persist lane commands and project waiting conditions into state-backed next items.

## Boundaries & Constraints

The single local daemon owns writes. Keep project isolation and both local MCP transports. Keep hosted and external workflow work outside this batch.

</frozen-after-approval>

## Code Map

- `src/plotroom/` contains these core entry points or additions — `core/lanes.py, core/roles.py, core/catalog.py`.
- Wire them through these repository paths — `app/config.py, adapters/store_sqlite, adapters/mcp/server.py`.

## Tasks & Acceptance

- [ ] Implement the core behavior and reuse the project writer for durable changes.
- [ ] Wire the normal entry points and verify the acceptance scenario in `tests/test_lanes.py`.

Given one fixture worker and an orchestrator, when both transports call lane tools and the daemon restarts, then ownership holds and the board preserves the recorded lane and acknowledgment.

## Implementation Notes

## Plan Change Log

## Review Triage Log

- Snapshot includes role-scoped current lane records and can page or fetch a named lane. The smoke verifies an active record after restart and a ready record after the waiting item clears. Mutating tools require caller-generated request IDs.

- A lane start checks work-item assignment inside the writer transaction, including when an older idle suggestion is used.

## Verification

- `uv run --locked --python 3.14 python -m pytest tests/test_lanes.py`
