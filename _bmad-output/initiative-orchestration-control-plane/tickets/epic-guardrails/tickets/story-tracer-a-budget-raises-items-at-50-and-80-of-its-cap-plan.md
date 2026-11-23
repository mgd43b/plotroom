---
title: "Budget spend and warning thresholds"
type: feature
ticket: 1
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

An orchestrator needs to register the cap and owning lane for a paid run and see the latest observed spend. The local fixture meter supplies cumulative observations with timestamps so the ledger can warn at 50% and 80%.

Refs #101.

## Boundaries & Constraints

- Accept a named key scope only. The fixture and its port carry observations only.
- Compare normalized as-of times. Retain the first reading when two readings have equal timestamps and select only the newest observation in a poll.
- Keep the newest spend, including a downward correction, and retain any threshold that already fired.
- Keep the observation and its consequences in one writer transaction. Isolate the fixture by project. The real provider is tracked separately in #102.

</frozen-after-approval>

## Code Map

- `src/plotroom/ports/usage_meter.py` and `adapters/usage_fixture.py` define the read-only and deterministic meter.
- `core/budgets.py` owns budget records, argument validation, and threshold decisions. Accept a positive finite cap and require a current lane. A run identifies one budget in its project.
- `app/runtime.py` reads the optional fixture outside the writer and applies the poll within the tick. `app/serve.py` injects a meter per project.
- `core/catalog.py` registers the writer command and system event kinds. `core/ledger.py` and `adapters/mcp/server.py` expose a fresh, orchestrator-only status query. Use typed MCP parameters and the usual command receipt only for mutations.
- `ports/store.py`, `core/unit_of_work.py`, and `adapters/store_sqlite/` persist budget state with migration 0018. The provisional parent is local main revision 0016. Verify the actual parent and coordinate with Epic 6 before the gate.
- `UnitOfWork.raise_item` computes keys as kind plus subject. Use kind `budget` and subject `<id>:<percent>`. A unique system threshold event keeps that item once-only after acknowledgment. Avoid lane activity changes for meter events.

## Tasks & Acceptance

**Execution**

- [x] Add the fixture meter, writer integration and budget projection/storage.
- [x] Register `budget_open` and `budget_status` on MCP and update the transport contract.
- [x] Exercise cumulative readings, newest selection, replay, validation, acknowledgment, and the populated migration.

**Acceptance Criteria**

- Given an authenticated orchestrator and active lane, when a budget is opened and readings cross 50% and 80%, then each threshold yields one durable item with its budget key.
- Given duplicate, overlapping, stale, and newer corrected fixture readings, when the runtime ticks, then status shows the latest spend, cap, as-of, and lane, and earlier warnings occur once even after acknowledgment.
- Given a worker token or an invalid cap or scope reference, when the corresponding tool is called, then authorization or validation rejects the call without recording the supplied value.

## Implementation Notes

- Each project receives its own meter through the daemon. UTC-normalized cumulative readings replace the projection only when newer. A unique event for each threshold is committed with its next item and the spend update.
- The transport rejects Boolean and string caps before the command runs. The command accepts only the named scopes `staging` and `production`.

## Plan Change Log

## Review Triage Log

- Medium · Patch. A stalled meter could delay other tick work. The poll now uses the tick interval as its deadline.
- Medium · Patch. The timeout regression verifies the writer tick, retained spend and warnings, and recovery on a later observation.
- Medium · Patch. A platform-dependent year below 1000 could fail on the next poll. The meter uses a fixed-width ISO timestamp and tests its round trip.
- Medium · Patch. A same-time wrong-meter fixture did not challenge grouping solely by run. The wrong meter now reports a newer sample.
- Low · Out of scope. The provider field is a validated meter label. Source discovery and provider credentials belong to later integration. The stored scope accepts only staging or production.
- Medium · Gate condition. Revision 0018 follows 0016 on the current main. The actual parent must be rechecked before the gate. The populated test exercises both budget scopes and routing.
- Low · Verified. MCP contract tests exercise fresh status and authorization. Daemon and ledger tests exercise the meter and resulting spend and warning items.

## Verification

- `uv run python -m pytest tests/test_budgets.py tests/test_transport_contract.py tests/test_migrations.py`
- Combined native review ran four independent lenses. Local verification passed on Python 3.14 and 3.15 with 828 tests each, plus focused migration, delivery, and transport regression checks. Ruff, Pyright, and the core dependency check passed.
