---
title: 'Track checklist completion and archive proposals'
type: feature
ticket: 3
created: '2026-11-23'
status: in-review
baseline_revision: '65ed0835eeab89276f197f375cc1ebdcd87dfc54'
route: full
route_source: auto
risk: medium
context: [AGENTS.md]
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

Track checklist completion and archive proposals as part of the local Epic 6 lane.

## Boundaries & Constraints

Follow the canonical story and Epic 6 Done-when criteria. Use local fixture evidence and the existing role, ledger and projection rules.

## Acceptance Criteria

Given an active checklist with open steps, when the orchestrator tries to archive the lane, then it receives the names of those steps. Given an owning worker or orchestrator, when it records completion of the last step, then exactly one archive item opens. Given a different worker, when it records a step, then authorization denies it. Given an empty typed READY, when its PR merges, then the item opens and local lane archive closes it.

</frozen-after-approval>

## Code Map and Tasks

- [x] `core/checklists.py`, `core/catalog.py` and `adapters/mcp/server.py` add checklist_done with a completion ref and an idempotent no-op for repeat completion. Keep mutation and consequences in the current unit of work.
- [x] `core/lanes.py` guards archive before mutation and preserves release and existing item clear. Share the exact archive proposal item identity and context with `core/trigger_actions.py`, including when a trigger and checklist reach it in either order.
- [x] `tests/test_checklists.py` exercises denied ownership, named refusal, ref, replay, archive-item reuse, zero-step merge, and local archive cleanup. No session archive is performed.

## Implementation Notes

Completion uses the declared ref unless the caller supplies a completion ref. Owner checks and request receipts stay in the central command path. Focused verification passed with named refusal, ownership, retries, archive proposal reuse, empty READY and cleanup.

## Plan Change Log

## Review Triage Log

## Verification

- `rtk proxy uv run --locked python -m pytest tests/test_checklist_lifecycle.py tests/test_trigger_actions.py tests/test_lanes.py tests/test_transport_contract.py`
