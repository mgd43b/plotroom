---
title: 'Full-text search over Switchboard messages'
type: feature
ticket: "1"
created: '2026-11-23'
status: built
baseline_revision: '169f3269e1a785d4823d7605efd1741a35ca17f2'
route: full
route_source: pinned
risk: medium
review: thorough
review_source: pinned
lenses_ran: [blind-hunter, edge-case-hunter, verification-gap, intent-alignment]
review_loop_iteration: 0
context: ['{project-root}/AGENTS.md']
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

The local daemon needs a searchable history of committed Switchboard messages. Add a project-bound Search port and SQLite FTS5 implementation, and expose it on the existing authenticated MCP surface.

## Boundaries & Constraints

- The event and its full-text row commit in the same writer transaction. A duplicate retains the original row.
- Search uses the credential's project and the existing board read restriction. Hits contain the actual event ID, kind and a short excerpt.
- The existing token estimate bounds the complete tool reply. The response reports omitted hits from both the result limit and the token budget.
- Scope is the Switchboard message tracer. Verification uses synthetic inputs.

## I/O & Edge-Case Matrix

| Scenario | Input or state | Expected result |
| --- | --- | --- |
| Record lookup | Committed message and a word from it | Actual ID, kind and matching excerpt |
| Replay or rollback | Duplicate upstream identity or a failed unit of work | One original hit or zero rolled-back hits |
| Large match set | More matches than fit the limit or response budget | Bounded reply with exact omitted count |
| Project isolation | Two credentials and distinct fixture bodies | Each search sees only its own project |

</frozen-after-approval>

## Code Map

- `core/ledger.py` constructs the UOW for inputs, commands and trigger evaluation. `core/unit_of_work.py` assigns identity and sequence before inserting events.
- The SQLite store owns project files and WAL connections. Alembic owns schema changes and backups.
- MCP `board_access` already enforces credential scope and excludes drain. `core/snapshot.py` estimates the actual MCP envelope.

## Tasks & Acceptance

- [x] Add the Search port in `ports/search.py` and call it from each UOW append. Let each daemon project construct one search binding; test-only ledgers can opt in explicitly.
- [x] Add the FTS5 migration and SQLite implementation in `adapters/store_sqlite`. Use reserved revision 0020 with current parent 0017. Keep virtual tables out of Alembic autogenerate.
- [x] Wire `app/serve.py`, `app/config.py` and `adapters/mcp/server.py` for scoped typed search and configurable response budgeting (`search_budget`, default 2000, minimum 256).
- [x] Prove the matrix in `tests/test_search.py` and exercise search in the existing four-way transport contract.
- [x] Verify a populated prior-revision database upgrades to one head with its existing records intact.

Given a committed fixture, when a caller searches over HTTP or stdio, then only the authenticated project supplies hits and the whole successful reply stays within the configured budget.

## Implementation Notes

- Each SQLite store binds reads to its project and accepts index writes only on its own writer transaction. The FTS row ID is the ledger sequence, which joins to the stored public ID and kind.
- Search accepts a literal FTS phrase and optional kind filter. Counts and hits share one committed read snapshot. The budget includes the indented success envelope and the omitted count.
- Revision 0020 creates the FTS table for new appends. The populated upgrade preserves prior ledger events. Historical reindexing is tracked separately.

## Plan Change Log

## Review Triage Log

The four review lenses checked the combined #114/#115 diff. This plan records the lexical search findings.

| Finding | Verdict | Disposition and evidence |
| --- | --- | --- |
| FTS shadow tables broke replay snapshots | Medium | The full suites failed on both interpreters when the snapshot queried shadow tables by rowid. Preserve every table and order WITHOUT ROWID tables by their primary key. |
| A foreign event could alias a local FTS sequence | Medium | Select the body from the matching local event in the writer transaction and prove a same-sequence foreign event is rejected. |
| Alembic used a name filter only | Low | Wire the AD-5 object filter and verify the virtual table and its exact shadow tables are excluded from autogenerate. |
| Failed index insert had no rollback test | Medium | Exercise failure inside the normal append and verify the ledger stays unchanged. |
| A wide first excerpt could cause an empty result page | Medium | Fit a shorter first excerpt while retaining rank and accurate omission. |
| A very long match can be clipped | Low | Bounded excerpts can clip an unusually long phrase. The fixture covers the normal case of the matching word after a long adjacent token. |
| Pre-upgrade messages are absent from FTS | Scope | Confirmed against the populated upgrade fixture. The separately assigned historical backfill is #117. |
| Transport test seeds the ledger directly | Scope | The four-way transport contract covers real ledger-to-MCP wiring. The existing Switchboard adapter already appends these events through the same ledger; its fixture tests cover adapter intake. |
| Opening Kodus found excerpts can lose matches after embedded NULs | Medium | Normalize NULs to separators only in the FTS projection. The regression verifies the matching word appears and the ledger retains the original body. |

## Verification

- `uv run pytest tests/test_search.py tests/test_transport_contract.py tests/test_migrations.py` passed 33 tests.
- `uv run pytest tests/test_config.py tests/test_switchboard.py tests/test_epic3_e2e.py` passed 65 tests.
- `uv run ruff check src tests`, `uv run ruff format --check src tests`, `uv run pyright`, and `uv run python tests/depcheck.py` passed.

- Managed Python 3.14 and 3.15 full suites passed after the initial review fixes (864 tests each). The final focused run passed on both interpreters (68 tests each), including both HTTP size-split paths. Final Ruff, Pyright, dependency and whitespace checks passed.
- [Built in PR #164](https://github.com/mgd43b/plotroom/pull/164)
