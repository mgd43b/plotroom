---
title: 'Store and evaluate operator goals'
type: feature
ticket: '6'
created: '2026-11-23'
status: built
baseline_revision: '78471ab1fd992a3a1ea209ecdadff0eb5a622b06'
route: full
route_source: auto
risk: medium
review: quick
review_source: pinned
lenses_ran: [quick]
review_loop_iteration: 0
context: []
---

<frozen-after-approval reason="human-owned intent">

## Intent

The orchestrator can store a named goal and read whether it holds and why every remaining issue fails.

## Boundaries & Constraints

Validate before persistence. The named or list read uses one committed project view and produces no writes. Ship the first goal as an example.

| Input or state | Expected result |
| --- | --- |
| Invalid goal or worker write | Refuse without modifying the stored goal |
| Fixture milestone with two failing issues | Report both with failed clauses |
| All selected issues meet the rule | Report done |

</frozen-after-approval>

## Code Map

- src/plotroom/core/operator_goals.py, core/catalog.py — register goal_set and evaluate stored goals
- src/plotroom/adapters/store_sqlite/goals_sql.py — load goals and typed projection facts in one read
- adapters/mcp/server.py, README.md — expose and explain operator goals
- tests/test_transport_contract.py — invoke all tools through local transports

## Tasks & Acceptance

- [x] src/plotroom/core/operator_goals.py, core/catalog.py — register goal_set and evaluate stored goals
- [x] src/plotroom/adapters/store_sqlite/goals_sql.py — load goals and typed projection facts in one read
- [x] adapters/mcp/server.py, README.md — expose and explain operator goals
- [x] tests/test_transport_contract.py — invoke all tools through local transports
- [x] Verify the fixture cases and record the results here.

Given a persisted first goal and fixture issues across milestones, when assignment and blocking facts change, then the readonly report names every failing scoped issue and reports done only when all selected issues satisfy the goal.

## Implementation Notes

- The local checkpoint adds the goal table and a read-only store snapshot of operator goal text and projected facts. Public registration follows the owner-approved #83 grammar. Reuse its pure parser/evaluator and restore the recovered goal persistence selectively. Migration 0023 derives from 0022. Run goal reads in one committed read snapshot, surface incomplete issue projection as unavailable, and keep the snapshot budget independent of unbounded failure detail in the goal report.

## Plan Change Log

## Review Triage Log

- Independent inspection and worker-session Codex review found legacy and stale relationship evidence could still certify blockers. Goal evaluation requires verified issue and tree pagination. Failed relationship batches retain unrelated evidence and mark the affected issues for retry.
- Pagination checks reject duplicate rows and shifting page boundaries. The bounded terminal probe allows exactly the configured maximum data pages.
- Goal results include the issue observation time, and a known GitHub poll failure returns unavailable. Only blocker predicates activate goal-driven relationship polling. Re-enabling it advances the relationship generation in the goal writer transaction. The provider fixture verifies that a poll started before reactivation cannot publish its result afterward.
- Explicit GitHub references establish lane ownership. Numeric or dotted lane refs with no durable GitHub identity remain unavailable, including after a work-item source change.

- Opening Kodus feedback prompted isolated relationship retries, conditional boundary checks, and loading only the evidence requested by each goal.

## Verification

- Local checkpoint: `uv run python -m pytest -q tests/test_goal_projection.py` passed all three projection and storage fixtures.

- The populated 0022 upgrade checks retained search and decision evidence. Goal fixtures cover exact text, atomic rejection, read-only evaluation, complete failure lists, and the existing snapshot budget. The transport contract exercises goal tools and worker-token refusal on HTTP and stdio in both eras.

- `uv run python -m pytest -q tests/test_human_queue.py tests/test_goal_language.py tests/test_transport_contract.py`
- [GitHub story](https://github.com/mgd43b/plotroom/issues/87)
- Built: https://github.com/mgd43b/plotroom/pull/175
