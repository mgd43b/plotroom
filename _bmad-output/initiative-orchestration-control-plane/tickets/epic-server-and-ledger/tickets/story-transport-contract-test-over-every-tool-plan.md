---
title: 'Transport contract test over every tool'
type: feature
ticket: 15
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

**Problem:** A registered tool can regress independently on HTTP or stdio across protocol eras.

**Approach:** Run an exhaustive call matrix and stream/cancellation checks in the existing CI suite.

## Boundaries & Constraints

The single local daemon owns writes. Keep project isolation and both local MCP transports. Keep hosted and external workflow work outside this batch.

</frozen-after-approval>

## Code Map

- `src/plotroom/` contains these core entry points or additions — `adapters/mcp/server.py, adapters/mcp/proxy.py`.
- Wire them through these repository paths — `tests/conftest.py, .github/workflows/ci.yml`.

## Tasks & Acceptance

- [ ] Implement the core behavior and reuse the project writer for durable changes.
- [ ] Wire the normal entry points and verify the acceptance scenario in `tests/test_transport_contract.py`.

Given both protocol eras and local transports, when the full tool set and stream behavior are probed, then every tool works and the bare 401 and lock exit contracts hold.

## Implementation Notes

## Plan Change Log

## Review Triage Log

- Native Codex review. The contract test checks required mutation request IDs in both schemas and both transports, and adds a regression that measures actual MCP text and fetches full lane and item details.

## Verification

- `uv run --locked --python 3.14 python -m pytest tests/test_transport_contract.py`
