---
title: 'Settle linked asks with decisions'
type: feature
ticket: '4'
created: '2026-11-23'
status: in-review
baseline_revision: '3ecf20da2ba64a6e5e2e18f4e41b50db05b3fab0'
route: full
route_source: auto
risk: low
review: quick
review_source: pinned
lenses_ran: [quick]
review_loop_iteration: 0
context: []
---

<frozen-after-approval reason="human-owned intent">

## Intent

Recording a human decision links it to governed tickets and settled asks.

## Boundaries & Constraints

Validate all settled ask IDs before mutating. Keep the decision, links and item transitions atomic. Local settlement preserves the GitHub issue state.

| Input or state | Expected result |
| --- | --- |
| Existing linked ask with stale item | Close stale item and show both links |
| Invalid ask ID | Roll back all decision effects |
| Same request retried | Preserve IDs and state |

</frozen-after-approval>

## Code Map

- core/human_queue.py — validate and settle asks and expose linked decision IDs
- adapters/store_sqlite/human_queue_sql.py — read governed decisions and linked asks
- tests/test_human_queue.py — cover rollback, settlement, filters and visible items

## Tasks & Acceptance

- [x] core/human_queue.py — validate and settle asks and expose linked decision IDs
- [x] adapters/store_sqlite/human_queue_sql.py — read governed decisions and linked asks
- [x] tests/test_human_queue.py — cover rollback, settlement, filters and visible items
- [x] Verify the fixture cases and record the results here.

Given a stale ask, when a decision settles it and governs its ticket, then the stale item closes, asks shows the decision link, and decisions filtered by the ticket shows the ask link.

## Implementation Notes

- The four-ticket queue slice was ported to main at `b65da56b75be68d436ae6acd30a6199555826fbc`. The original implementation checkpoint remains available locally.

- All ask IDs are resolved before the decision is inserted. A decision stores the exact governed references and settlement IDs. Each ask exposes its settling decision ID. Existing checks close in the same writer transaction.

## Plan Change Log

## Review Triage Log

| Finding | Verdict | Route | Evidence |
| --- | --- | --- | --- |
| Settlement roles, atomicity, link visibility, and migration | No findings | Verified | Both native reviewers inspected the write path and the populated upgrade fixtures. |

## Verification

- On the ported slice, run the full suite on Python 3.14 and 3.15. The transport contract exercises loopback HTTP and stdio against fixture data.

- Integration fixtures cover exact ticket filtering, unknown-ask rollback, stale item closure and a repeated settlement refusal. The populated main-head upgrade fixture checks linked evidence after 0021.

- `uv run python -m pytest -q tests/test_human_queue.py tests/test_migrations.py`
- [GitHub story](https://github.com/mgd43b/plotroom/issues/86)
