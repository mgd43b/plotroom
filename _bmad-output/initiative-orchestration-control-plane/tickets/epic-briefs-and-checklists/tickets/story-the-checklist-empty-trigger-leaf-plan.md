---
title: 'Trigger once on the last completed checklist step'
type: feature
ticket: 4
created: '2026-11-23'
status: in-review
baseline_revision: '65ed0835eeab89276f197f375cc1ebdcd87dfc54'
route: full
route_source: auto
risk: low
context: [AGENTS.md]
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

Trigger once on the last completed checklist step as part of the local Epic 6 lane.

## Boundaries & Constraints

Follow the canonical story and Epic 6 Done-when criteria. Use local fixture evidence and the existing role, ledger and projection rules.

## Acceptance Criteria

Given a trigger for a lane without an active checklist, when it is created, then it remains open. Given the trigger and an active nonempty checklist, when the last step closes, then the committed event fires it once across daemon restarts and retries.

</frozen-after-approval>

## Code Map and Tasks

- [x] `core/checklists.py` appends a typed `core.checklist_empty` event keyed by lane and READY version only on the last open step transition. Empty READY activation opens its archive item independently.
- [x] `core/catalog.py` registers the event for the system. `core/triggers.py` adds a lane-validated event leaf using the existing sequence floor, stable action keys and transaction.
- [x] `tests/test_checklists.py` exercises restart and event/action receipts without external delivery.

## Implementation Notes

The versioned last-step event, action and trigger fire commit together. A focused restart test verifies the open trigger before activation and the event and single action after restart.

## Plan Change Log

## Review Triage Log

## Verification

- `rtk proxy uv run --locked python -m pytest tests/test_checklist_lifecycle.py tests/test_triggers.py tests/test_trigger_predicates.py tests/test_trigger_recovery.py`
