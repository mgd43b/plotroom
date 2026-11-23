---
title: 'Actions, action runner and ticks'
type: feature
ticket: 7
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

**Problem:** Committed effects need durable retries and periodic clock inputs.

**Approach:** Enqueue cause-keyed actions inside their originating transactions and run their handlers outside the writer.

## Boundaries & Constraints

The single local daemon owns writes. Keep project isolation and both local MCP transports. Keep hosted and external workflow work outside this batch.

</frozen-after-approval>

## Code Map

- `src/plotroom/` contains these core entry points or additions — `core/actions.py, core/catalog.py, app/runtime.py`.
- Wire them through these repository paths — `adapters/store_sqlite, app/serve.py`.

## Tasks & Acceptance

- [ ] Implement the core behavior and reuse the project writer for durable changes.
- [ ] Wire the normal entry points and verify the acceptance scenario in `tests/test_actions.py`.

Given a fixture action and fake clock, when the action fails repeatedly and the daemon restarts, then attempts are bounded, one acknowledgment closes its item, and ticks follow the configured cadence.

## Implementation Notes

## Plan Change Log

## Review Triage Log

- Native Codex review. An interrupted handler does not consume a completed attempt. At the final retry limit the same action key can still deliver after restart. The next tick targets the first future interval after a slow source refresh.

## Verification

- `uv run --locked --python 3.14 python -m pytest tests/test_actions.py`
