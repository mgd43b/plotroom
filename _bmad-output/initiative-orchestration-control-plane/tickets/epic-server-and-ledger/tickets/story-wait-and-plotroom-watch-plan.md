---
title: 'Wait and plotroom watch'
type: feature
ticket: 9
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

**Problem:** A session needs to wake on the next visible change and detect missing watchers.

**Approach:** Use the project cursor and bounded wait calls, with the CLI carrying the cursor across timeouts.

## Boundaries & Constraints

The single local daemon owns writes. Keep project isolation and both local MCP transports. Keep hosted and external workflow work outside this batch.

</frozen-after-approval>

## Code Map

- `src/plotroom/` contains these core entry points or additions — `core/ledger.py, core/watchers.py, app/cli.py`.
- Wire them through these repository paths — `adapters/mcp/server.py, adapters/store_sqlite, app/runtime.py`.

## Tasks & Acceptance

- [ ] Implement the core behavior and reuse the project writer for durable changes.
- [ ] Wire the normal entry points and verify the acceptance scenario in `tests/test_watch.py`.

Given a two-second server cap and a CLI watcher, when the watcher passes multiple caps and a new item commits, then it wakes within one second and a later tick flags its absence.

## Implementation Notes

## Plan Change Log

## Review Triage Log

## Verification

- `uv run --locked --python 3.14 python -m pytest tests/test_watch.py`
