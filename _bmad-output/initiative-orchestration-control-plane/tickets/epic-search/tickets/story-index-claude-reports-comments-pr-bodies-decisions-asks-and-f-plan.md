---
title: 'Index every local search source'
type: feature
ticket: 2
created: '2026-11-23'
status: built
baseline_revision: 'fd50ef15eb92f354918c262d77cc5a0acd161894'
route: full
route_source: pinned
risk: high
review: thorough
review_source: pinned
lenses_ran: [blind-hunter, edge-case-hunter, verification-gap, intent-alignment]
review_loop_iteration: 0
context: ['{project-root}/AGENTS.md']
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

Index Switchboard messages, Claude turn reports and messages, GitHub issue comments and PR bodies, human decisions and asks, and flake candidates within their local project's existing writer transaction. Keep caller-visible hits to real source IDs, kind, and excerpt. Decisions use the human's exact words. Respect exact kind filters and the existing orchestrator-only visibility of decisions and asks.

## Boundaries & Constraints

Use fixture-only records. No live Claude or external provider calls, hosted work, README changes, or guardrail edits. This is the first of five ordered slices in a single PR. Historical backfill and new semantic ranking follow in #117 and #118.

| Input | Expected result |
| --- | --- |
| Each supported source commits | Search finds a word from the source under its own stable ID and kind |
| A resource edit or replay | The current body wins and replay produces one hit |
| Writer rollback | No new or replaced hit survives |
| Explicit kinds, second project, or a non-orchestrator | Filters and access apply before ranking, limit, and omitted count |

</frozen-after-approval>

## Code Map

- `core/unit_of_work.py` owns append, insert_decision, save_ask, and save_github_resource. Use exact words, ask text, and complete current COMMENTS per comment scope. For FLAKE_EVENT include meaningful check fields because check-level body can be empty. Use actual evt IDs on ledger events and actual decision/ask IDs.
- `core/github.py:_project_pull` has the accepted ObservedPull body after the stale guard, including body-only edits and when no gate workflow is configured. Also handle complete PULL_TEXT snapshots as appropriate without stale overwrites. Preserve existing intake/polling rules and avoid new network fetches. Stable GitHub references must include repository plus native PR or comment identity.
- `ports/search.py` and `adapters/store_sqlite/{store,ledger_sql,search_sql}.py` provide the project-bound port, transaction guard and FTS. `adapters/mcp/server.py` knows the role. `core/ledger.py` owns query validation. Use public kind names `decision`, `ask`, `github.issue_comment`, `github.pr_body`, and existing qualified event kinds for Switchboard, Claude and flakes.
- Reserve Alembic 0022, parent actual main 0018. Add ordinary `search_records` keyed by (kind, record_id) with INTEGER PRIMARY KEY AUTOINCREMENT sequence and exact stored body. Reuse message_fts by seeding metadata at its existing rowids via a join to existing Switchboard events in upgrade. Existing FTS hits must survive upgrade. Reuse rowids for FTS join and keep mutation atomic. A changed source gets a fresh sequence for #118 to follow; identical writes keep sequence. Preserve empty updates as records to prevent backfill restoring removed text. Keep existing embedding tables and follower operational in this slice. Expose an insert-if-absent mode for #117. Alembic's FTS exclusion stays intact.
- The SQLite event index must validate the actual local event before deriving its text, so fabricated or foreign bodies cannot be indexed through a valid local transaction. Keep the existing guard and replay coverage.

## Tasks & Acceptance

- [x] Extend the Search port and SQLite index for record identity, updates, and exact kind filtering in both result and total queries.
- [x] Wire immutable event and mutable projection/domain sources through the existing writer. Add role-aware search in Ledger and MCP without changing the tool arguments.
- [x] Add focused fixture cases in `tests/test_search.py` or `tests/test_search_sources.py` for source shapes, exact words, kind filter, edit, idempotence, isolation, and rollback. Verify 0022 preserves existing FTS hits over populated 0018 while keeping a single head.

Given saved records with distinct source kinds and projects, when a matching authenticated search is called, then each hit links to the current source and forbidden or nonmatching kinds contribute no hits or omissions.

## Implementation Notes

- Source rows keep verbatim bodies. FTS replaces embedded NULs with spaces so text beyond a NUL remains searchable. Replays keep the original search sequence, while changed bodies receive a new sequence. Empty bodies retain a source row for backfill.
- Pull body references use `owner/repo#number` and comment references append the native `#issuecomment-id` anchor to the issue reference. Pull detail snapshots must match the current pull's head, version, and update time.

## Plan Change Log

## Review Triage Log

- Filtered-out records affected lexical rank through FTS5 corpus statistics. Use per-record ordering and verify that an unrelated private decision cannot move a worker's top hit.
- Same-version PR detail could overwrite a newer list-poll body. The accepted list snapshot owns live body changes and detail can seed a previously absent row.

## Verification

- `uv run --locked python -m pytest -q tests/test_search.py tests/test_search_sources.py tests/test_search_embeddings.py tests/test_migrations.py tests/test_transport_contract.py`
- `uv run --locked ruff check src tests` and `uv run --locked pyright`
- At this slice, the targeted suite passed 70 tests, including populated upgrade, authenticated decision and ask visibility, and preexisting message vector compatibility. Ruff, format checks, and Pyright passed.
- Combined verification after review remediations passed 986 tests on each of managed Python 3.14 and 3.15. Ruff, Pyright and the core dependency check passed.
