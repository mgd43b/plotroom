---
title: 'Verify the search epic through the local daemon'
type: feature
ticket: 7
created: '2026-11-23'
status: in-review
baseline_revision: '05b2af64a73babe369ae7522c5f71de69b2f5355'
route: full
route_source: pinned
risk: medium
review: thorough
review_source: pinned
lenses_ran: []
review_loop_iteration: 0
context: ['{project-root}/AGENTS.md']
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

Verify every Search epic Done-when criterion over a real loopback daemon and authenticated MCP tool calls. Exercise absent and fake HTTP embedder modes with only synthetic fixture data.

## Boundaries & Constraints

Use fixtures and localhost. Scope is local search. Preserve the owned boundaries of guardrails and the README lane.

</frozen-after-approval>

## Code Map

- `tests/test_daemon.py` supplies service start/stop helpers. `tests/conftest.py` supplies the real authenticated MCP client. `tests/test_search_embeddings.py` supplies reusable loopback provider fixtures. No test contacts Claude, a live provider, or GitHub.
- `tests/test_migrations.py:_migrations_at` builds a populated historical fixture. Reuse the search backfill fixture if it avoids duplicate schema-seeding code.
- `core/human_queue.py:decide` accepts synthetic chat citations and returns the actual decision ID. A fixture semantic query can use disjoint vocabulary. Synchronize on committed checkpoint/signal or controlled fixture events and make the restart/model swap deterministic.

## Tasks & Acceptance

- [x] Add one real-daemon test per Epic Done-when line in `tests/test_search_e2e.py`. Cover scoped lexical hits and budget with two credentials, a semantic decision paraphrase and model reindex with fake HTTP, and discovery of the oldest record after a populated historical upgrade with the embedder absent.
- [x] Run the suite with the combined lane verification.

Given the real daemon and synthetic fixture data, when each epic acceptance scenario is exercised through authenticated MCP on localhost, then all three requirements hold through the same search tool.

## Implementation Notes

- Both project credentials search the same loopback endpoint and query. Each credential sees its own event ID and excerpt. A larger query checks the serialized MCP reply's token budget and counts omitted local hits.
- The semantic scenario records decisions through authenticated MCP. A loopback HTTP fixture holds the replacement model's batch to verify lexical fallback during reindexing, then changes the vector coordinates to verify ranking with the replacement vectors.
- The historical scenario creates two message records at revision 0017, before the search schema, then starts the daemon with the embedder disabled. It waits for the committed event backfill checkpoint and verifies the original record's ID and excerpt through MCP.

## Plan Change Log

## Review Triage Log

## Verification

- `uv run --locked python -m pytest -q tests/test_search_e2e.py tests/test_transport_contract.py` passed all 16 tests.
- The combined search, source indexing, backfill, embedding, epic acceptance, migration, and transport suites passed all 80 tests.
- `uv run --locked ruff check src tests`, `uv run --locked pyright`, and the Ruff format check for `tests/test_search_e2e.py` passed.
