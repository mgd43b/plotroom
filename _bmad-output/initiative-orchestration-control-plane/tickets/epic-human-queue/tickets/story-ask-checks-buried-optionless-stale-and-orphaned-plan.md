---
title: 'Surface and clear ask checks'
type: feature
ticket: '3'
created: '2026-11-23'
status: built
baseline_revision: '3ecf20da2ba64a6e5e2e18f4e41b50db05b3fab0'
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

The orchestrator sees actionable items when an ask meets one of four independently tracked conditions.

## Boundaries & Constraints

Use projected issue and comment identities and event-clock time. Use positive evidence for burial and known comment history before concluding there was no reply.

| Input or state | Expected result |
| --- | --- |
| Newer nonhuman comment or no options | Raise one item per condition |
| Past configured age or closed with no human reply | Raise one item per condition |
| Reopened or human reply clears a condition | Close the corresponding active item |

</frozen-after-approval>

## Code Map

- src/plotroom/core/ask_checks.py and src/plotroom/core/asks.py — derive and reconcile deterministic ask conditions
- core/ledger.py, core/unit_of_work.py — reconcile in the same input transaction on relevant writes/ticks
- src/plotroom/adapters/store_sqlite/asks_sql.py — read scoped projected comment facts

- src/plotroom/adapters/github/details.py and src/plotroom/adapters/github/issue_details.py — confirm per-issue history through the existing GitHub read adapter

## Tasks & Acceptance

- [x] src/plotroom/core/ask_checks.py and src/plotroom/core/asks.py — derive and reconcile deterministic ask conditions
- [x] core/ledger.py, core/unit_of_work.py — reconcile in the same input transaction on relevant writes/ticks
- [x] src/plotroom/adapters/store_sqlite/asks_sql.py — read scoped projected comment facts
- [x] Verify the fixture cases and record the results here.

Given fixture asks spanning all four conditions, when projection updates and repeated ticks run, then exactly one actionable item per true condition remains and conditions that clear close their items.

## Implementation Notes

- Provider fixtures cover missing history, pagination, wrong scope, malformed links, shifted pages, and a version change after a scan with the expected count.

- The four-ticket queue slice was ported to main at `b65da56b75be68d436ae6acd30a6199555826fbc`. The original implementation checkpoint remains available locally.

- Changes to projected issues, comments and comment cursors and committed ticks reconcile state-backed items. The orphan check matches a complete paginated history to the closed issue’s reported comment count and confirms the same version after the scan. The repository-wide cursor can provide individual comments but does not prove an issue has no reply. A confirmed human reply or reopened issue clears a previously inferred orphan.

## Plan Change Log

- Integrated main at `60ac5ffd247dd21fff701d066db55df480e53c91` and attached `0021` to its merged `0020` head.
- After the clerk import merged at `7e1fa13016bffa6e46a9f9e165c1f26537164ef0`, attached `0021` to its merged `0019` head.

## Review Triage Log

| Finding | Verdict | Route | Evidence |
| --- | --- | --- | --- |
| Repository-wide cursors could omit a human reply | Medium | Patch | The poller confirms complete paginated comment history for the specific closed issue version. Reconciliation consumes the source facts and coverage marker in the same writer transaction. |

| Deleted human reply could suppress an orphan item | High | Patch | The closed-issue scan commits its comment IDs with its verified version. Only current, verified members count for orphan checks. |
| Unrelated comment history scanned during writes | Medium | Patch | A partial SQLite index constrains the comments lookup by repository and issue number. The query plan uses both keys. |
| One SQL lookup per closed issue during reconciliation | Low | Patch | The reconciliation writer reads the relevant version and membership rows in one query. |

## Verification

- On the ported slice, run the full suite on Python 3.14 and 3.15. The transport contract exercises loopback HTTP and stdio against fixture data.

- Fixture coverage isolates all four conditions, repeated ticks and item closures. Pure fixture cases cover incomplete history and ambiguous comment ordering.

- `uv run python -m pytest -q tests/test_human_queue.py tests/test_ask_checks.py`
- [GitHub story](https://github.com/mgd43b/plotroom/issues/85)
- Built: https://github.com/mgd43b/plotroom/pull/165
