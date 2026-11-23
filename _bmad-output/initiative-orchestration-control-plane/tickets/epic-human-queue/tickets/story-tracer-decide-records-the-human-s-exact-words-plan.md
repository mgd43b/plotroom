---
title: 'Record the human’s exact words'
type: feature
ticket: '1'
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

An orchestrator can cite structured GitHub comment or chat evidence and retrieve the unchanged words by governed ticket.

## Boundaries & Constraints

GitHub evidence needs the exact project repository, issue and numeric comment ID, known author matching both who and configured human_login, and an exact body substring. Chat retains the supplied citation and words.

| Input or state | Expected result |
| --- | --- |
| Missing projected comment | Retryable unavailable with no record |
| Wrong issue, author, or words | Refusal with no record |
| Chat or validated GitHub comment | Exact UTF-8 round trip and stable receipt |

</frozen-after-approval>

## Code Map

- core/human_queue.py — validate evidence and build decisions
- core/catalog.py, app/config.py, app/serve.py — register writer and load optional human login
- adapters/store_sqlite/human_queue_sql.py, ports/store.py, core/unit_of_work.py — persist and query decisions
- adapters/mcp/server.py — surface decide and decisions

## Tasks & Acceptance

- [x] core/human_queue.py — validate evidence and build decisions
- [x] core/catalog.py, app/config.py, app/serve.py — register writer and load optional human login
- [x] adapters/store_sqlite/human_queue_sql.py, ports/store.py, core/unit_of_work.py — persist and query decisions
- [x] adapters/mcp/server.py — surface decide and decisions
- [x] Verify the fixture cases and record the results here.

Given an authenticated worker or a missing GitHub comment, when decide is called, then the tool refuses before an event or decision is committed. Given accepted evidence, when it is read and retried, then exact words and the stable decision ID are returned.

## Implementation Notes

- The four-ticket queue slice was ported to main at `b65da56b75be68d436ae6acd30a6199555826fbc`. The original implementation checkpoint remains available locally.

- Use migration `0021` after the current main head `0019` with a dedicated local decision table. Add the concrete SQLite transaction and store methods along with their port declarations.
- GitHub citation input: `{"kind": "github_comment", "repository": "owner/repo", "issue": 42, "comment_id": 9001}`. Read `github_resource(repository, COMMENTS, str(comment_id))` and validate the record's exact scope. Chat citation input: `{"kind": "chat", "ref": "opaque-source-reference"}`. Reject unknown citation fields and types. GitHub logins are case insensitive, so compare casefolded identity but keep submitted spelling. Require the configured human when present for chat and preserve the citation and quote. Existing projects without human config stay usable.
- Use `core.decide` through the command registry and response convention. Return `decision_id` on write and a decisions list on read. Use existing orchestrator read restrictions and support exact ticket reference filtering. Preserve the list values and shapes in decision records. The settlement implementation will be built in #86.
- The ownership for this change also covers `tests/test_human_queue.py`, including rollback, retries, cross-issue evidence, Unicode normalization mismatch, and chat preservation. Use the existing fixture project and store patterns. The parent lane will extend transport coverage separately.

## Plan Change Log

- Integrated main at `60ac5ffd247dd21fff701d066db55df480e53c91` and attached `0021` to its merged `0020` head.
- After the clerk import merged at `7e1fa13016bffa6e46a9f9e165c1f26537164ef0`, attached `0021` to its merged `0019` head.

## Review Triage Log

| Finding | Verdict | Route | Evidence |
| --- | --- | --- | --- |
| Untyped MCP citation and missing worker-token assertion | Low | Patch | Declared strict GitHub and chat citation shapes and added worker-token refusals through both transports. |

| Filtered decisions loaded all quotes before filtering | Medium | Patch | SQL matches the exact governed ticket reference before returning rows. |

## Verification

- On the ported slice, run the full suite on Python 3.14 and 3.15. The transport contract exercises loopback HTTP and stdio against fixture data.

- `uv run python -m pytest -q tests/test_human_queue.py`
- Python 3.14.10 passed all 16 decision fixtures, including a replay after reopening the database, rollback after a failed event write, cross-issue and author refusals, and exact UTF-8 and chat citation round trips.
- `uv run python -m pytest -q tests/test_migrations.py tests/test_config.py` passed all 63 fixtures, including optional project login configuration and migration from a populated claims predecessor.
- [GitHub story](https://github.com/mgd43b/plotroom/issues/82)
- Built: https://github.com/mgd43b/plotroom/pull/165
