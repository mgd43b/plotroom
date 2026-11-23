---
title: "Check readiness from the merge gate"
type: feature
ticket: 1
created: '2026-11-23'
status: built
baseline_revision: 331a79b360c289137c42e71ee5ccd2890cd89c72
route: full
route_source: auto
risk: high
review: quick
review_source: pinned
lenses_ran: [quick]
review_loop_iteration: 0
context:
  - AGENTS.md
  - .claude/skills/clerk/scripts/safemerge.sh
  - .claude/skills/clerk/scripts/lib.sh
---

<frozen-after-approval reason="human-owned intent">

## Intent

Expose a typed, read-only gate query with the checked head, stable reason codes, clerk exit class and the observation watermark.

## Boundaries & Constraints

Use one consistent SQLite read snapshot. Refuse missing configuration, unavailable or stale PR data and unreadable check evidence.

</frozen-after-approval>

## Code Map

- `src/plotroom/core/merge_gate.py` — Implement the ordered rule registry and rule 1 using latest checks per name.
- `src/plotroom/core/ledger.py`, `src/plotroom/ports/store.py`, `src/plotroom/adapters/store_sqlite/store.py` — Read PR and check facts together without invoking the writer or triggers.
- `src/plotroom/app/config.py`, `src/plotroom/app/serve.py`, `src/plotroom/adapters/mcp/server.py` — Supply project policy and authorize the clerk and orchestrator query.

## Tasks & Acceptance

- [x] `src/plotroom/core/merge_gate.py` — Implement the ordered rule registry and rule 1 using latest checks per name.
- [x] `src/plotroom/core/ledger.py`, `src/plotroom/ports/store.py`, `src/plotroom/adapters/store_sqlite/store.py` — Read PR and check facts together without invoking the writer or triggers.
- [x] `src/plotroom/app/config.py`, `src/plotroom/app/serve.py`, `src/plotroom/adapters/mcp/server.py` — Supply project policy and authorize the clerk and orchestrator query.
- [x] Add deterministic observations and acceptance cases under `tests/`.

Given fixture check histories, when a clerk or orchestrator queries a PR, then pending takes precedence over minimum and failure, and the result includes the head and as_of. Missing minimum, stale data and an unreadable resource refuse. Repeated attempts count once; ignored pending checks still refuse; canceled and skipped checks count as complete.

## Implementation Notes

- Per-project GitHub configuration accepts `min_checks` and an optional list of exact `ignore_checks`. A missing minimum refuses the query. The configured poll interval sets the freshness window, with a separate bound for unchanged PR rows covered by a current incremental poll.
- The read pins the PR, PR cursor and head-scoped check facts in one SQLite transaction. The current GitHub polling error also forces a refusal. The returned watermark uses the oldest participating observation and retains the last observed head on refusal.
- Rule 1 uses the existing GitHub check-run projection and groups attempts by check name, as required by the story.

- Verified the isolated base at `331a79b360c289137c42e71ee5ccd2890cd89c72`. Its Python 3.14.10 suite passed all 553 tests.
- The gate consumes the Check Runs projection supplied by PR #147. The ticketer has the compatibility finding for repositories that also use legacy status contexts.

## Plan Change Log

## Review Triage Log

- Medium, fixed. A detail-poll rate limit marks GitHub unavailable immediately while preserving previously observed check facts.
- Medium, fixed. The result includes the PR row timestamp in the watermark even when a more recent polling cursor exists.
- Medium, fixed. Both supported protocols and transports now exercise a configured pending-check verdict with a check-derived watermark.
- High, fixed. The Check Runs reader validates provider totals, so a missing page cannot leave a partial projection eligible.
- Opening Kodus review, fixed. A run supersedes only earlier observations of the same name and confirmed app. Distinct names still determine the minimum.

## Verification

- `uv run python -m pytest -q tests/test_merge_gate_rule1.py tests/test_github_details.py tests/test_transport_contract.py` — 42 passed.
- Built: https://github.com/mgd43b/plotroom/pull/152
