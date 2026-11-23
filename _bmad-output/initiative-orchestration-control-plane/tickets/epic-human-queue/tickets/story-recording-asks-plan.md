---
title: 'Record asks on GitHub issues'
type: feature
ticket: '2'
created: '2026-11-23'
status: built
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

An orchestrator can record and retrieve a structured ask associated with an already projected comment on an exact issue.

## Boundaries & Constraints

The writer produces only local state. A missing projection returns unavailable. Structured option text is retained verbatim.

| Input or state | Expected result |
| --- | --- |
| Projected issue and comment | Store issue, text, options and ID |
| Absent projected comment | Unavailable and no record |
| Comment belongs to another issue | Invalid and no record |

</frozen-after-approval>

## Code Map

- core/human_queue.py, core/catalog.py — implement and register ask
- adapters/store_sqlite/human_queue_sql.py — project asks
- adapters/mcp/server.py — expose ask and asks

## Tasks & Acceptance

- [x] core/human_queue.py, core/catalog.py — implement and register ask
- [x] adapters/store_sqlite/human_queue_sql.py — project asks
- [x] adapters/mcp/server.py — expose ask and asks
- [x] Verify the fixture cases and record the results here.

Given a test comment on a configured project issue, when ask is accepted and read, then the ask exposes text, options, and the exact comment identity.

## Implementation Notes

- The four-ticket queue slice was ported to main at `b65da56b75be68d436ae6acd30a6199555826fbc`. The original implementation checkpoint remains available locally.

- The SQLite writer records one ask per project repository/comment. An orchestrator can revise the options against the same exact projected comment with a new request ID. Existing receipts keep their ask ID.

## Plan Change Log

- Integrated main at `60ac5ffd247dd21fff701d066db55df480e53c91` and attached `0021` to its merged `0020` head.

## Review Triage Log

| Finding | Verdict | Route | Evidence |
| --- | --- | --- | --- |
| MCP coerced non-integer references | Medium | Patch | Both ask identifiers now use strict integer validation. Local transport fixtures exercise malformed values against a matching projected comment. |

| Cached comment could cite an issue absent from projection | High | Patch | The ask writer requires a complete issue projection with the cited issue and checks for retry after the issue becomes available. |

## Verification

- On the ported slice, run the full suite on Python 3.14 and 3.15. The transport contract exercises loopback HTTP and stdio against fixture data.

- Fixture coverage for writer roles, orphan-safe missing projection, wrong issue, exact text, options and retry; exercised by both MCP transports.

- `uv run python -m pytest -q tests/test_human_queue.py`
- [GitHub story](https://github.com/mgd43b/plotroom/issues/84)
- Built: https://github.com/mgd43b/plotroom/pull/165
