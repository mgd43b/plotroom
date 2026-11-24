---
title: 'Continue vector indexing after permanent input rejection'
type: bugfix
ticket: 8
created: '2026-11-24'
status: in-review
baseline_revision: '995b3198aa75d74bada58c908f31c8cf22922897'
route: full
route_source: pinned
risk: high
review: thorough
review_source: pinned
lenses_ran: []
context: ['{project-root}/AGENTS.md']
---

<frozen-after-approval reason="human-owned intent">

## Intent

Approved choice: “Record the rejection, keep the text searchable, and index later records (Recommended)”.

A confirmed permanent rejection applies only to the exact source revision and configured model and dimension. Record it atomically with vectors and the durable cursor. Preserve verbatim source text and let later accepted records complete. Keep transient and global failures on the existing retry path.

## Boundaries & Constraints

Use fixture-only SQLite and loopback HTTP. Scope is local search. Preserve the writer, provider isolation and unrelated runtime behavior.

| Scenario | Expected |
| --- | --- |
| Singleton request rejected permanently | Durable record-specific rejection and later vector progress |
| Credentials, downtime, malformed reply, or local response cap | Retry the batch without a permanent disposition or cursor advancement |
| Reopen or replay | Persisted dispositions and vectors survive; no duplicate work |
| Search with a rejected lexical match | Original authorized text stays searchable; accepted records remain available to semantic queries |

</frozen-after-approval>

## Code Map

- `adapters/embedding_http.py` already bisects oversized batches. Classify only definite per-record evidence. HTTP 413 after isolating a single input without Retry-After is a defined payload rejection. Client response-size limits, ambiguous 4xx, auth, bad JSON, 429, 5xx and temporary 413 are not individual permanent rejections. Keep ordered mixed outcomes and the existing transactional behavior when any split fails transiently.
- `ports/search.py`, `core/search.py`, `core/unit_of_work.py` and SQLite `embedding_sql.py`/store own the ordered outcomes and serialized expected-cursor commit. Use typed rejection, persist safe reason and model/dimension plus current record revision in migration 0024 after 0023, and remove stale dispositions when the revision is replaced. Preserve foreign-project and stale-revision guards.
- Keep rejected text in FTS and make rejection observable. Preflight and final query use the model and authorized kinds: known rejects do not hold readiness for unrelated records. If the literal query matches a rejected record, use observable lexical fallback so normal MCP search returns it. Recheck this in the final snapshot after HTTP, and count only ranked vector hits when serving semantic results. Never send provider error bodies or source text to logs.
- `tests/test_search_embeddings.py` has the loopback provider and restart/race fakes. `tests/test_migrations.py` covers populated upgrades. Explain confirmed rejection, lexical availability and transient retry in README.

## Tasks & Acceptance

- [x] Wire definite per-record rejection, ordered partial success, and atomic vector/rejection persistence.
- [x] Verify an oversized input stays lexical while later valid records are embedded and retrievable, preserving cursor restart and provider/global retry.
- [x] Prove a populated 0023 upgrade has one head and preserves existing records, vectors and cursor.

## Implementation Notes

Implemented the approved choice “Record the rejection, keep the text searchable, and index later records (Recommended)”. A singleton HTTP 413 without `Retry-After` records the typed `input_too_large` disposition for the exact revision and configured model and dimension. The writer stores rejections and vectors with the expected cursor in one transaction. Superseding the source removes its prior disposition.

Authorized literal matches against a rejected revision use `embedding_rejected` lexical fallback. The final vector query rechecks the match in its SQLite snapshot. Semantic counts include only records with vectors. Fixture coverage confirms the split request remains atomic on HTTP 400, 401, 413 with `Retry-After`, 429, and 500, as well as malformed and oversized responses.

## Review Triage Log

## Verification

- The embedding, authenticated MCP search, and migration suites passed on Python 3.14.10 and 3.15.2, with 63 tests on each.
- Ruff lint and format checks, Pyright, and `git diff --check` passed.
