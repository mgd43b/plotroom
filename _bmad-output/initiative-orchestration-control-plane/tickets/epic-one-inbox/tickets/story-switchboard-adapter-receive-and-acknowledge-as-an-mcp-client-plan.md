---
title: Switchboard adapter receive and acknowledge
type: feature
ticket: 7
created: '2026-11-23'
status: in-review
baseline_revision: fe1077b741a32c3eb017dc54918902d192c631c9
route: full
route_source: auto
risk: high
review: thorough
review_source: pinned
lenses_ran: []
review_loop_iteration: 0
context: []
---

<frozen-after-approval reason="human-owned intent">

## Intent

**Problem:** Local Switchboard mail does not reach the durable project ledger.

**Approach:** Run a protected plotroom participant on the configured board using stdio MCP and acknowledge each claim after its transaction commits.

## Boundaries & Constraints

Use a scripted board for verification. Relay receipts stay in memory. Keep the participant opt-in and use the existing writer and lane lifecycle.

## I/O & Edge-Case Matrix

| Scenario | Input | Behavior |
| --- | --- | --- |
| Delivery | Configured sender on an active lane | One event on that lane, with the claim acknowledged |
| Redelivery | Same message ID and fresh receipt after interruption | Original event preserved, fresh receipt acknowledged |
| Startup outage | Relay process unavailable | Retry with backoff and receive after recovery |

</frozen-after-approval>

## Code Map

- `core/catalog.py` registers events. `core/ledger.py` resolves writer completion after commit.
- `core/unit_of_work.py` resolves the current worker lane inside that same transaction.
- `app/config.py` loads optional project provider settings. `app/serve.py` wires and stops provider tasks.

## Tasks & Acceptance

- [x] Register and append typed messages in core, looking up sender addresses from configuration.
- [x] Add a stdio SDK participant and wire it into daemon startup and teardown.
- [x] Drive the participant with an isolated fixture through commit, interruption, and recovery.

Given one claimed message and an interruption before acknowledgment, when the restarted participant handles a new claim, then the original event is still the only stored event and the new receipt is acknowledged.

## Implementation Notes

The isolated stdio child exercised startup recovery and a fresh receipt following restart. The combined batch review is pending.

## Plan Change Log

## Review Triage Log

## Verification

- `uv run python -m pytest tests/test_switchboard.py tests/test_config.py`
