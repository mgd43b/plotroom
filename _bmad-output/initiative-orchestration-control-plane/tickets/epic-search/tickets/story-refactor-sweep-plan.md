---
title: 'Address verified local search cleanup'
type: refactor
ticket: 6
created: '2026-11-23'
status: built
baseline_revision: 'e59d434dc9d476b581eb51ab6ecd9f88f8ed38ba'
route: full
route_source: pinned
risk: low
review: thorough
review_source: pinned
lenses_ran: [blind-hunter, edge-case-hunter, verification-gap, intent-alignment]
review_loop_iteration: 0
context: ['{project-root}/AGENTS.md']
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

Complete the verified follow-ups from the #114 and #115 plans and this lane's review. Keep the behavior and boundaries of the existing search stories.

## Boundaries & Constraints

Use fixtures and localhost. Scope is local search. Preserve the owned boundaries of guardrails and the README lane.

</frozen-after-approval>

## Code Map

- The foundation found the FTS excerpt can lose the tail of a matching phrase that would fit in the excerpt. Verify with a long token and bounded surrounding context, and improve match placement in `adapters/store_sqlite/search_sql.py` with a targeted regression in `tests/test_search.py`.
- The embedding foundation recorded that an irreducibly rejected single message retries at its durable checkpoint. Story #118 preserves lexical availability during that condition, and exact text fidelity prohibits silently shortening or dropping records. Record this residual limitation.
- Unexpected storage failures retain committed checkpoints and follow the existing daemon input failure lifecycle. Preserve that policy and record any verified review finding that would need a distinct runtime change.
- Audit the foundation plan logs before making changes. Historical lexical backfill and MCP semantic integration are already the scope of #117 and #118.

## Tasks & Acceptance

- [x] Verify the documented cleanup against source, fix the bounded excerpt case if reproducible, and record the disposition of residual findings.
- [x] Run the focused search tests and include full verification for the combined PR.

Given a match up to the configured excerpt cap with long surrounding text, when the search returns the hit, then the actual matching text remains visible within the cap.

## Implementation Notes

- Full-text search selects and ranks a bounded set of matching records before asking FTS5 to highlight their complete match spans. The excerpt comes from the indexed body and reserves enough space for the first highlighted span when it fits within 240 characters. A longer span starts at the match and is clipped at the cap.
- Both regression cases failed with the previous excerpt. The 240-character token lost its tail to preceding context. The 239-character phrase exceeded the 12-token snippet window.

## Plan Change Log

## Review Triage Log

- The MCP budget fallback could clip a match that fit without context. Preserve the matched span when trimming the first ranked excerpt.
- Routed existing single-message provider rejection and the generic project-input storage recovery policy to the coordinator as follow-up candidates. Neither changes the current durability or lexical fallback behavior.

| Finding | Disposition |
| --- | --- |
| A matching phrase can lose its tail within the character cap | Fixed and covered by both regression cases. |
| A provider can permanently reject one record | Routed to the ticketer as a separate input-policy candidate. `HttpEmbedder.embed` splits size failures only when the batch can be divided. The singleton stays unchanged, and `follow_search_embeddings` retries at the durable checkpoint. Queries that need the incomplete index report `index_pending` and fall back to lexical. Any skip or truncation policy would need an explicit decision on preserving source text and advancing the cursor. |
| Unexpected storage failure can end the background input | Routed to the ticketer as a separate runtime recovery candidate. `commit_backfill` and vector writes retain the last committed checkpoint on rollback. The daemon's existing background-input lifecycle leaves restart to resume there. A recovery change should prove bounded retries, a visible failure indication, and restart from the same checkpoint without duplicate records. |
| Historical lexical indexing and MCP semantic integration | Covered by #117 and #118. |

## Verification

- `uv run --locked python -m pytest -q tests/test_search.py tests/test_search_backfill.py tests/test_search_embeddings.py tests/test_search_sources.py tests/test_transport_contract.py` passed 55 tests.
- `uv run --locked ruff check src tests`, `uv run --locked pyright`, and the Ruff format check for the changed Python files passed.
- SQLite's query plan shows the limited rank set materialized before the second FTS lookup by row ID and phrase.
- Combined verification after review remediations passed 986 tests on each of managed Python 3.14 and 3.15. Ruff, Pyright and the core dependency check passed.
