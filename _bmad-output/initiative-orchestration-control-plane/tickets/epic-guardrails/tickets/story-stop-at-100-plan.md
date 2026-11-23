---
title: "Stop a lane at its budget cap"
type: feature
ticket: 2
created: 2026-11-23
status: built
baseline_revision: "c5cbfdca5cabcca23d22ad8e74e33a6b1a297de6"
route: full
route_source: pinned
risk: medium
review: thorough
review_source: pinned
lenses_ran: ["blind-hunter", "edge-case-hunter", "verification-gap", "intent-alignment"]
review_loop_iteration: 0
context: ["{project-root}/AGENTS.md"]
---

<frozen-after-approval reason="human-owned intent">

## Intent

An orchestrator needs a cap item when an observed run reaches 100% and the owning lane needs a stop message. The committed budget threshold drives the existing send action and channel adapters.

Refs #103.

## Boundaries & Constraints

- Queue the cap item and the stop action at most once per budget in the observation transaction.
- Use only scripted Switchboard and native-drain stand-ins on temporary local state. The work uses deterministic fixture spend.
- A previously archived lane must not direct a stop to a worker reassigned to a new lane.

</frozen-after-approval>

## Code Map

- `core/budgets.py` records the 100% threshold and queues the existing `send` action keyed `budget:<id>:100`, with the owning worker, the lane binding, and the literal message `stop` (initiative R20). Use the same budget key for the orchestrator item.
- `core/unit_of_work.py::enqueue_action` currently expects a kind-prefixed stable key. Permit the budget key explicitly for a matching system threshold cause while preserving the other key checks.
- `app/runtime.py::_dispatch` already maps send to Switchboard or native outbox, passing the action key through. Check the budget's pinned lane before delivery. A native outbox row and its final check should share the writer transaction. Keep the existing Switchboard network call outside the writer.
- `tests/test_trigger_actions.py`, `tests/switchboard_child.py`, and `tests/test_trigger_recovery.py` show how to run isolated channels.

## Tasks & Acceptance

**Execution**

- [x] Add the cap effect in `core/budgets.py` and support its key in `core/unit_of_work.py`.
- [x] Preserve the two existing delivery paths and guard the lane binding in `app/runtime.py`.
- [x] Prove pending delivery survives a restart and a later replay creates no extra stop in `tests/test_budgets.py`.

**Acceptance Criteria**

- Given two fixture-funded lanes on the two delivery channels, when each reaches the cap and the runtime restarts before dispatch, then each stop and orchestrator item has its budget key and appears once across replay and restart.
- Given a budget whose lane has been archived, when the cap arrives or its queued action is drained, then its orchestrator item remains and a different lane receives no stop.

## Implementation Notes

- The threshold event deduplicates the orchestrator item and queued stop. The cap item still opens when the budget lane has already been archived.
- Dispatch checks the worker's current lane against the queued lane. Native staging checks ownership in the writer transaction that inserts the outbox row. Switchboard checks ownership before handing off to the relay, which uses the budget key for idempotency.

## Plan Change Log

## Review Triage Log

- High · Patch. The native outbox could lease a staged stop after the target lane was archived. Lease and readiness now require a current matching lane for budget stops.
- High · Patch. The Switchboard lane check preceded the registration wait. The relay path now rechecks the pin after registration and before submission.
- High · Patch. An archived lane could previously leak its stop past either dispatch check. Both handoff paths now check the same pinned budget lane.
- Medium · Patch. A missing channel could exhaust the stop's retry budget. A pinned stop for a live lane remains pending until a channel is configured.
- Medium · Patch. A single overwritten relay record hid duplicate requests. The fixture records request history and verifies the durable action key.
- Medium · Patch. The relay restart case now verifies recovery after the reply is interrupted and checks per-key delivery.
- Medium · Patch. The native restart case exercises an expired lease after delivery and verifies deduplication when the same keyed stop is re-leased. An expired lease for a reassigned lane cannot wake the drain or be leased again.
- Medium · Existing handoff. A previously issued lease or a relay request in flight retains the delivery protocol's handoff semantics.

## Verification

- `uv run python -m pytest tests/test_budgets.py tests/test_trigger_actions.py tests/test_trigger_recovery.py` passed all 18 tests.
- Combined native review ran four independent lenses. Local verification passed on Python 3.14 and 3.15 with 828 tests each, plus focused migration, delivery, and transport regression checks. Ruff, Pyright, and the core dependency check passed.
- Built: https://github.com/mgd43b/plotroom/pull/160
