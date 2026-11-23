---
title: 'Resumable embeddings for local search'
type: feature
ticket: "4"
created: '2026-11-23'
status: built
baseline_revision: '169f3269e1a785d4823d7605efd1741a35ca17f2'
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

Build local vector indexing and lookup for Switchboard messages. Use a project-scoped HTTP Embedder port and a durable follower of committed event sequence numbers. Keeping HTTP off the writer preserves ledger availability during provider delays.

## Boundaries & Constraints

- Embeddings are disabled unless a project configures an endpoint, model and dimension.
- A completed batch's vectors and cursor commit together through the project writer. Event input never waits for embedding.
- Persist float32 vectors as BLOBs keyed by event ID, model and dimension. Use SQLite distance functions for semantic lookup and the configured model and dimension only.
- The existing MCP search remains lexical in this lane. Expose project-bound vector lookup behind the embedding index port for later search-by-meaning integration. Use synthetic records and a loopback fake HTTP endpoint for verification.

## I/O & Edge-Case Matrix

| Scenario | Input or state | Expected result |
| --- | --- | --- |
| Disabled | No project embedder | Full-text search and no provider work |
| Provider stalled or down | Appends during an outstanding or failing request | Appends commit, failed batch leaves its checkpoint unchanged, and recovery catches up |
| Restart | Persisted checkpoint for the same configuration | Continue above the last committed sequence |
| Reconfiguration | Different model or dimension | New keyed cursor starts from zero and recomputes eligible records |
| Invalid response | Wrong count, dimension or non-finite values | Retry without writing a partial batch |

</frozen-after-approval>

## Code Map

- `Ledger.events_after` reads committed events and `commit_signal` wakes followers. Capture the signal before reading.
- `ProjectRuntime` manages input lifetimes. `app/serve.py` supplies the inputs and stops them before writer shutdown.
- Keep reusable follower mechanics separate from message selection and embedding. Commit through a system UOW using the expected cursor so a stale attempt cannot overwrite progress. Reuse the existing search hit shape.
- Extend the tracer's SQLite store with embedding cursor, vector batch and lookup operations. The SQLite adapter dependency check permits search SQL in the existing `store_sqlite` package. The architecture pins sqlite-vec 0.1.9.

## Tasks & Acceptance

- [x] Implement `ports/search.py`, the HTTP adapter, and the reusable committed-sequence follower in `core/search.py`.
- [x] Add vectors and per-key cursor to the same additive migration as the tracer. Guard the cursor and persist the batch through the writer.
- [x] Wire optional configuration (per-project `embedder` table, `url`, `model`, `dimension`) and follower startup in `app/config.py` and `app/serve.py`. Retain the already applied sqlite-vec pin in `pyproject.toml` and `uv.lock`.
- [x] Exercise the matrix and semantic result ranking with loopback-only HTTP fixtures in `tests/test_search_embeddings.py`. Verify mismatched project rejection and rollback of vectors with the cursor on a batch write error.

Given a configured fixture embedder and committed message vectors, when the internal vector lookup receives the paraphrase's vector, then SQLite ranks that model and dimension's vectors and returns hits only from the bound project.

## Implementation Notes

Each configured project starts a cancellable input that reads committed event pages, waits for HTTP outside the writer, and commits one batch with its expected cursor. Separate model and dimension keys preserve restart progress and permit reindexing. Local vector lookup loads sqlite-vec 0.1.9 on demand and ranks by L2 distance. The loopback provider fixtures passed on managed Python 3.14 and 3.15.

## Plan Change Log

## Review Triage Log

The four review lenses checked the combined #114/#115 diff. This plan records the embedding findings.

| Finding | Verdict | Disposition and evidence |
| --- | --- | --- |
| Provider reply had no response-size limit | Medium | Bound the streamed response before parsing. |
| Excessive JSON nesting could end the follower | Medium | Convert decoder recursion failure to a retryable provider error and exercise recovery. |
| URL validation differed from the actual HTTP client | Medium | Validate with the client parser and translate invalid URLs to typed configuration and request errors. |
| Float conversion can overflow before packing | Low | Guard the finiteness check together with the pack operation. |
| A malformed internal batch could checkpoint past actual events | Medium | Validate through sequence and record membership in the writer transaction. |
| NUL truncated the SQLite string excerpt | Low | Clip the retrieved excerpt in Python, preserving valid text after NUL. |
| Extension load could expose raw driver errors | Medium | Return an availability error for vector lookup. The FTS path does not load sqlite-vec. |
| A page of excluded events had no independent fixture | Medium | Verify cursor-only advancement before an eligible message on a later page. |
| Duplicate response indexes had no retry fixture | Low | Add the malformed response to loopback recovery coverage. |
| Restart and reindex fixtures were smaller than a page | Medium | Exercise multi-page catch-up with an excluded first page and a committed vector on the next page. |
| A provider or decoded response can reject a multi-message batch for size | Medium | Split the HTTP request off-writer and retain original message text and result order. Loopback coverage proves a single atomic write after both halves finish. |
| A provider can permanently reject one message | Scope | Preserve the durable checkpoint and retry. The configured endpoint must accept the supplied message texts and support the configured model and dimension. Truncating or skipping would change the embedded content. |
| Arbitrary storage failures stop the input | Low | Writer errors leave the last committed checkpoint intact. The daemon has the existing project input shutdown and restart behavior. Provider outage recovery is handled in the follower. |
| Batch commits evaluate existing trigger hooks | Low | The same serialized system unit of work maintains the project's trigger rules. The network wait completes outside it, and cursor-only pages make one bounded transaction each. |
| Natural-language MCP semantic query is unavailable | Scope | Confirmed internal vector ranking with fixture-provided query vectors. The separately assigned query integration is #118. |
| The index can commit an interval with no vectors | Low | A cursor-only page is valid. The follower selects eligible messages from committed events, verifies the provider count, and supplies all resulting vectors. The writer verifies every supplied record belongs to the local interval. |
| Opening Kodus found response parsing could delay the daemon event loop | Medium | Dispatch bounded response decoding and validation to a worker thread. The loopback regression blocks decoding while the ledger commits another input. |

## Verification

- `uv run pytest tests/test_search.py tests/test_search_embeddings.py tests/test_migrations.py`
- `uv sync --locked` and the repository lint, formatting, typing and dependency checks

- Managed Python 3.14 and 3.15 full suites passed after the initial review fixes (864 tests each). The final focused run passed on both interpreters (68 tests each), including both HTTP size-split paths. Final Ruff, Pyright, dependency and whitespace checks passed.
- [Built in PR #164](https://github.com/mgd43b/plotroom/pull/164)
