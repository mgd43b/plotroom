---
title: Reconcile late merge SHA evidence
type: bugfix
ticket: 15
created: '2026-11-27'
status: built
baseline_revision: '67e2e40f8545707bc38ea8c0483b320479939eab'
route: full
route_source: auto
risk: medium
review: quick
review_source: pinned
lenses_ran: [quick]
context:
  - '{project-root}/AGENTS.md'
---

<frozen-after-approval reason="human-owned intent">

## Intent

Known merged PRs can age out of incremental list polling before GitHub includes their merge SHA. Recover a verified SHA by a bounded read-only detail pass. Retain the existing merge attribution and leave incomplete evidence pending.

## Boundaries and constraints

Keep list and ordinary PR detail cursors, gate decisions, live operations and available GitHub write capabilities independent of this pass. An established SHA stays authoritative. Use repository, PR number, merged state and known head to validate explicit provider PR merge identity. Keep timeline IDs, test-merge SHAs, PR heads and branch tips out of identity selection.

## I/O and edge-case matrix

| Given | When | Then |
| --- | --- | --- |
| Complete merged row with empty SHA older than the list floor | Exact provider detail returns the merge OID | Fill just the SHA and finalize a pending gate observation once. |
| Missing, null, invalid or mismatched provider details | Read or replay | Keep the SHA pending for a later read. |
| More pending rows than the per-cycle cap | Repeated polls or restart | Ordinary polling runs first. Attempt history gives older rows a later turn. |
| Concurrent list or detail result changes the row | Apply proof found earlier | Re-read current state and preserve established identity. |
| Historical merge and repeated valid detail | Backfill and restart | Keep PR events and action counts unchanged. |

</frozen-after-approval>

## Code map and execution

- Production implementer owns `src/plotroom` and the MemoryStore mirror in `tests/fakes.py`. The parent adds other tests, documentation and the canonical ticket refinement.
- `adapters/github/details.py` runs optional backfill at the tail of the normal detail poll after ordinary inputs, sharing cooldown. Cap at two PRs per repository per poll, at most two requests per PR. Use `api.pull_merge_sha(client, token, pull)`. Failed reads are contained and payloads never logged. Propagate genuine rate limit to existing cooldown, including GraphQL rate-limit errors.
- `adapters/github/read.py` queries exact repository and PR for merged/state, known head and explicit GraphQL `mergeCommit.oid` via its existing credential, bounded JSON reader and query transport. A validated null commit may fall back to exact REST PR detail with merged evidence and strict base repository/number/head match. REST explicit merge SHA carries merge, squash and rebase semantics. Malformed or mismatched GraphQL proof stops that candidate. The transport performs no mutations.
- `adapters/store_sqlite/{github_sql,store}.py`, `ports/store.py`, `core/ledger.py`, and `tests/fakes.py` expose a bounded query for complete merged rows lacking SHA. Use existing `github_fetch_cursors` per-number attempt records under a new resource prefix, ordered by last attempt or PR updated time, then attempt generation and number. This keeps a restart from resetting priority, including a pinned clock. Record an attempt even when no proof was found. Use no migration.
- `core/github.py` re-reads inside the single writer transaction and verifies the same immutable target, still merged and missing SHA. Record attempt metadata separately. Save only the merge SHA through `UnitOfWork.save_pull_request`, keeping observation freshness and existing pending attribution. Guard against a later conflicting ordinary list result replacing an established merge identity. The writer revalidates full SHA shape.
- `tests/test_merge_sha_backfill.py` verifies provider validation, polling budgets and cooldown, late recovery, cursor invariants, concurrent established values, restart and replay on disposable stores. Existing controller fixtures cover pinned/gated and historical bookkeeping.

## Implementation notes

- Backfill runs after ordinary detail reads, with two candidates and at most four provider requests. Each candidate gets at most three seconds, and the combined optional provider time is at most one quarter of the configured gate freshness window. Authenticated GraphQL rate-limit responses use the usual details cooldown.
- Attempt timestamps and generations live under dedicated per-PR cursor keys. The normal list and detail cursors are independent. A SHA-only projection update completes pending attribution through the existing reconciler and leaves full gate evaluation to the ordinary inputs.
- Writer revalidation and list projection preserve merge identities supplied by a concurrent observation. No schema or credentials change is required.

## Plan change log

## Review triage log

- Medium, patched. Independent review found that older attempted rows could starve other rows while local time lagged provider time. The selector clamps each ordering timestamp to one poll instant and uses generation as a tie-breaker. The fixture proves progress across restart with a pinned, provider-ahead clock.
- Verification strengthened during follow-up. A synthetic gated merge already has one trigger action before backfill. The test asserts the same action remains, gated attribution completes exactly once, and list cursor data and other PR fields remain equal. Independent follow-up cleared both changes.

- Medium, opening Kodus. At a one-second poll interval, the fixed optional timeout exceeded the two-second gate freshness window. The scaled budget permits 250 ms per candidate in this case. Independent follow-up and the focused fixture verified the deadline.

## Verification

- `uv run --locked python -m pytest -q tests/test_merge_sha_backfill.py tests/test_github.py tests/test_github_details.py tests/test_controller_fixture_merge.py`
- Run Ruff, Pyright, the dependency check and both locked Python suites. Verify the pinned remote CI and exact main merge.
- After the review changes, the 64 affected tests passed under each locked Python version. The broader Python 3.14 run passed 1,346 tests before the clock-skew correction.
- After the automatic review follow-up, 65 affected tests passed on each locked Python version.
