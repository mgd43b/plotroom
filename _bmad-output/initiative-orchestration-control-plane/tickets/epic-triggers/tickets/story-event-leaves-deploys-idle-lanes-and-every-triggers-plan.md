---
title: 'Event predicates and recurring triggers'
type: feature
ticket: 4
created: '2026-11-23'
status: built
baseline_revision: '3ecf20da2ba64a6e5e2e18f4e41b50db05b3fab0'
route: full
route_source: auto
risk: medium
review: thorough
review_source: pinned
lenses_ran: [native]
review_loop_iteration: 0
context: []
---

<frozen-after-approval reason="human-owned intent">

## Intent

Add message and deployment events, clock-based lane idleness, and durable every-trigger edges.

## Boundaries & Constraints

Use deterministic local fixtures and committed typed facts. External actions, hosted work, and real deployment and drain acceptance stay outside this build.

</frozen-after-approval>

## Code Map

- `src/plotroom/core/triggers.py` — Evaluate fresh events after the trigger cursor, persist whole-predicate truth for every mode, and derive one unique action cause per occurrence. Evaluate idleness against the latest persisted tick and lane activity.
- `src/plotroom/core/unit_of_work.py and src/plotroom/ports/store.py` — Expose scoped event reads in the writer transaction.
- `src/plotroom/adapters/store_sqlite/ledger_sql.py and src/plotroom/adapters/store_sqlite/triggers_sql.py` — Query scoped facts and persist mode, last truth, event cursors, and existing fired cursor.
- `src/plotroom/adapters/store_sqlite/migrations/versions/0015_recurring_triggers.py` — Add recurring state defaults and event indexes to the current main head. Preserve existing triggers and the 0014 reservation.
- `src/plotroom/core/catalog.py and src/plotroom/adapters/mcp/server.py` — Accept once and every. Keep once the default and return the mode in inventory.
- `tests/test_trigger_predicates.py and tests/test_migrations.py` — Prove replay, restart, composed edges, clock cycles, and a populated upgrade.

## Tasks & Acceptance

- [x] `src/plotroom/core/triggers.py` — Evaluate fresh events after the trigger cursor, persist whole-predicate truth for every mode, and derive one unique action cause per occurrence. Evaluate idleness against the latest persisted tick and lane activity.
- [x] `src/plotroom/core/unit_of_work.py and src/plotroom/ports/store.py` — Expose scoped event reads in the writer transaction.
- [x] `src/plotroom/adapters/store_sqlite/ledger_sql.py and src/plotroom/adapters/store_sqlite/triggers_sql.py` — Query scoped facts and persist mode, last truth, event cursors, and existing fired cursor.
- [x] `src/plotroom/adapters/store_sqlite/migrations/versions/0015_recurring_triggers.py` — Add recurring state defaults and event indexes to the current main head. Preserve existing triggers and the 0014 reservation.
- [x] `src/plotroom/core/catalog.py and src/plotroom/adapters/mcp/server.py` — Accept once and every. Keep once the default and return the mode in inventory.
- [x] `tests/test_trigger_predicates.py and tests/test_migrations.py` — Prove replay, restart, composed edges, clock cycles, and a populated upgrade.

Given a recurring trigger and local typed facts, when messages replay or a lane stays idle across ticks, then the action count is unchanged until a new message or an actual active-to-idle cycle occurs.

## Implementation Notes

[Built in PR #154](https://github.com/mgd43b/plotroom/pull/154)

- Each every trigger keeps its cursor and truth in the writer transaction and assigns a unique action cause to each rising edge. Switchboard sender identity comes from configuration and worker-submitted Claude messages from the authenticated role. lane_idle_minutes configures the quiet time and defaults to 15. The latest persisted tick and lane activity determine the idle state. Migrations preserve open and fired once records.

## Plan Change Log

## Review Triage Log

- Native review reproduced a mixed any_of trigger that stayed latched after a message arrived while the level predicate held. The writer records a separate pending-event cursor and the test verifies rearming across restart. The idle query uses a partial index for historical command receipt lanes and reads the latest tagged event and receipt independently.

- The review after the main merge found that a message in an unsatisfied nested branch could be lost during a true plateau. The writer now tracks per-leaf cursors for satisfied branches and preserves events waiting on other clauses.

## Verification

Run `uv run python -m pytest -q tests/test_trigger_predicates.py tests/test_migrations.py`.

The integrated local suites passed 737 tests on each of Python 3.14 and 3.15. Ruff, Pyright for both versions, and the core dependency check passed.
