---
title: 'Resume historical records into local search'
type: feature
ticket: 3
created: '2026-11-23'
status: in-review
baseline_revision: 'e5026784290ab18daf434f88e28828932bebf489'
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

Populate searchable records created before local search coverage. Resume the bounded backfill from a durable project checkpoint and commit index writes and checkpoint together through the writer. Use actual retained data and stable source IDs. Run without an embedder.

## Boundaries & Constraints

Use synthetic ledgers and committed source snapshots. Production access, providers, guardrails, and README are outside scope.

| Input | Expected result |
| --- | --- |
| An upgraded populated file | Every retained supported historical source is eventually searchable |
| Backfill interrupted between batches | Restart begins after the last committed checkpoint and returns the oldest record exactly once |
| A failed batch or a live edit among backfill reads | A failed write leaves the cursor unchanged. Replay remains idempotent and newer text wins |

</frozen-after-approval>

## Code Map

- `core/search.py:committed_batches` is the existing capture-before-read follower. Reuse it for committed event pages and add finite historical operation so a completed backfill does not leave a waiting background task. Keep the foundation embedding follower behavior in this slice.
- `adapters/store_sqlite/search_sql.py` and `core/search_sources.py` from #116 already own shared index/source conversion. Backfill existing `events`, typed `decisions` and `human_asks`, and the existing GitHub projection. Use the stable actual source identity and structured body extraction from #116. For PRs, restore the last known PULL_TEXT snapshot where it has the latest usable text and otherwise use the newest retained accepted lifecycle event body for that PR. Use current accepted issue-comment snapshots. Preserve newer live indexed content with insert-if-absent in the writer.
- `project_meta` can store a cursor per source or phase and per project, including a completion marker. Prefer stable source keys for projection pagination. Source pages, including ignored records, must remain bounded. Check the expected cursor and apply inserts plus advance together through one system writer submission.
- `app/serve.py` composes project background inputs independently of embedder config. No new runtime loop or external source fetch is needed. Keep the committed-page primitives reusable by ranking.
- `tests/test_migrations.py:_migrations_at` builds historical fixture revision chains. Current latest parent is verified 0018 from PR #160. The preceding #116 migration fixture preserves populated budget and route rows. Synthetic data should actually be persisted to that parent before upgrading, with at least one record owned by each historical path and preexisting search data where applicable.

## Tasks & Acceptance

- [x] Add durable and bounded backfill coordination in `core/search.py`, search port/adapter and daemon startup.
- [x] Add `tests/test_search_backfill.py` covering populated upgrade, exact restart state, interruption/rollback, stale-read protection and idempotence.
- [x] Preserve the one Alembic head and the foundation fixtures.

Given a project upgraded from a populated prior schema, when lexical backfill stops after a committed partial batch and resumes with the embedder absent, then the stored cursor advances from that checkpoint and every retained historical source can be found by its real ID exactly once.

## Implementation Notes

- Daemon startup schedules a finite lexical pass per project. Five durable `project_meta` checkpoints cover events, decisions, asks, current comment snapshots, and pull request bodies. The event pass captures a committed high-water mark. The remaining passes paginate by source keys.
- Each system writer transaction checks the expected checkpoint, inserts absent rows, and advances the checkpoint. A live edit that writes first keeps its newer index row even when the historical read is already in flight.
- Pull request backfill prefers a usable retained detail snapshot, including a body-only change newer than the last lifecycle event. Otherwise it uses the newest retained lifecycle body. Revision 0022 adds bounded lookup indexes and retains parent 0018.

## Plan Change Log

## Review Triage Log

- A failed PR detail refresh can retain usable body provenance. Historical scanning accepts still-provenance-matched retained attributes and keeps its lifecycle fallback.

## Verification

- `uv run --locked python -m pytest -q tests/test_search_backfill.py tests/test_search_sources.py tests/test_search.py tests/test_search_embeddings.py tests/test_migrations.py` passed all 61 tests.
- `uv run --locked ruff check src tests` and `uv run --locked pyright` passed.
