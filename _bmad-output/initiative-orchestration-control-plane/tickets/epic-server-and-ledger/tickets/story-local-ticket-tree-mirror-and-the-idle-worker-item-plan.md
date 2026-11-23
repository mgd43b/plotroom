---
title: 'Local ticket-tree mirror and idle workers'
type: feature
ticket: 13
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

**Problem:** The daemon needs a read-only source of work that an idle worker can start.

**Approach:** Read BMad readiness on ticks, mirror ready entries, and derive one actionable item per eligible worker.

## Boundaries & Constraints

The single local daemon owns writes. Keep project isolation and both local MCP transports. Keep hosted and external workflow work outside this batch.

</frozen-after-approval>

## Code Map

- `src/plotroom/` contains these core entry points or additions — `ports/work_items.py, core/work_items.py`.
- Wire them through these repository paths — `adapters/work_items_bmad.py, app/runtime.py, adapters/store_sqlite`.

## Tasks & Acceptance

- [ ] Implement the core behavior and reuse the project writer for durable changes.
- [ ] Wire the normal entry points and verify the acceptance scenario in `tests/test_work_items.py`.

Given a BMad repo fixture and an idle worker, when a tick reads ready entries and a lane starts, then the mirror persists, the tree is byte-for-byte intact, and the idle item closes.

## Implementation Notes

## Plan Change Log

## Review Triage Log

## Verification

- `uv run --locked --python 3.14 python -m pytest tests/test_work_items.py`
