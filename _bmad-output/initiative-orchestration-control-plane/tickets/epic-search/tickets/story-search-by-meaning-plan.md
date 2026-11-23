---
title: 'Rank local search by meaning'
type: feature
ticket: 5
created: '2026-11-23'
status: in-progress
baseline_revision: '86cc0c3a2aa0cdd46837df20504bd9ff2232b48e'
route: full
route_source: pinned
risk: high
review: thorough
review_source: pinned
lenses_ran: []
review_loop_iteration: 0
context: ['{project-root}/AGENTS.md']
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

Use the configured project embedder and SQLite vector distance for the existing authenticated search call. Find paraphrases across the expanded searchable sources. Give lexical results when the embedder is absent, its relevant index is stale, or the provider or vector extension is unavailable. Preserve kind, role and project filters, ranked limits, and exact omitted counts under the response budget.

## Boundaries & Constraints

Only synthetic records and a fake loopback HTTP provider are used for verification. Provider waits and vector generation stay off the single writer. Reuse the configured model/dimension identity, and keep record text unchanged for embedding.

| Input | Expected result |
| --- | --- |
| Current configured source vectors | L2 rank within the authorized kinds |
| Provider absent, down or returns malformed values | Lexical search stays available |
| Source edited while its vector is in flight | The old vector cannot represent the updated record and lexical fallback remains available |
| New model or dimension | Its own checkpoint builds the authorized searchable corpus |

</frozen-after-approval>

## Code Map

- `core/search.py` already follows committed batches and validates/packs provider vectors. Generalize the shared page reader to consume immutable search-record revisions as well as ledger pages; persist the existing model/dimension cursor and preserve cancel/retry semantics. `core/ledger.py` owns request validation and authorized kinds.
- `search_records.seq` is monotonic and changes with a searchable edit. Store vectors for that immutable revision, with a foreign key that removes superseded revisions. Reuse the existing writer-only commit boundary and expected cursor check. Advancing through a superseded revision must not install its vector or wedge the follower. A surviving record identity must still be checked for a stale/foreign batch.
- `adapters/store_sqlite/{embedding_sql,store}.py` own queries and sqlite-vec loading. Use one read snapshot for filtered readiness and distance results so edits during provider HTTP cannot revive stale hits. Model and dimension are mandatory predicates. Only authorized kinds count toward readiness and hits. Preserve or explicitly invalidate the 0020 vector cache with a documented deterministic rebuild path. Its old global event checkpoints cannot be applied to the expanded record-sequence domain. Apply schema work in the still-unpublished 0022 migration.
- On an upgrade, prefer carrying forward valid Switchboard vectors whose original event maps to the seeded current search record. Reset old cursor values and skip such valid vectors while advancing through the expanded corpus. During startup backfill, report lexical fallback while relevant history is still being indexed. Preserve the budget and route assertions in the populated migration fixture.
- `app/serve.py` wires per-project provider lifetime and shutdown. The MCP tool signature remains unchanged. Keep the lexical fallback clearly identifiable for configured projects, with a concise reason for pending or failed semantic lookup. Account for any new status metadata in `core/snapshot.py`'s token budget. Leave the successful baseline lexical payload available to configurations without an embedder.
- `tests/test_search_embeddings.py` already has a loopback fake, failure/recovery, model and cancellation fixtures. Adapt sequence assertions to the new durable source if necessary without weakening rollback, cursor-only batch, restart, or stale-result coverage.

## Tasks & Acceptance

- [x] Update the shared follower, vector-projection storage, migration and launch/shutdown wiring.
- [x] Implement off-writer semantic query, strict filtered readiness, and observable lexical fallback under the existing budget.
- [x] Cover the full matrix, model swap, and result budget with controlled fixture synchronization.

Given a recorded decision and current vectors, when an authenticated caller queries a disjoint-vocabulary paraphrase, then vector search returns the real decision ID. Given no usable provider or fresh relevant vector set, the same tool serves lexical hits with the same authorization and response cap.

## Implementation Notes

Vectors are bound to immutable search record revisions. The migration preserves compatible seeded cache rows and resets the former event-domain cursor. The follower skips preserved rows. The authenticated tool uses semantic rank only when current and reports lexical fallback as index_pending, provider_unavailable, or vector_unavailable. A second readiness check shares the vector query snapshot after the off-writer HTTP wait. The focused suite passed 73 tests with Ruff and Pyright.

## Plan Change Log

## Review Triage Log

## Verification

- `uv run --locked python -m pytest -q tests/test_search_embeddings.py tests/test_search.py tests/test_search_backfill.py tests/test_transport_contract.py tests/test_migrations.py`
- `uv run --locked ruff check src tests` and `uv run --locked pyright`
