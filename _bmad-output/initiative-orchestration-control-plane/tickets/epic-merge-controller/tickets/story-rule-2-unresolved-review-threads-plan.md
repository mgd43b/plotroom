---
title: "Require complete review-thread coverage"
type: feature
ticket: 11
created: '2026-11-23'
status: built
baseline_revision: 331a79b360c289137c42e71ee5ccd2890cd89c72
route: full
route_source: auto
risk: medium
review: quick
review_source: pinned
lenses_ran: [quick]
review_loop_iteration: 0
context:
  - AGENTS.md
---

<frozen-after-approval reason="human-owned intent">

## Intent

Extend the same gate registry to refuse unresolved threads or missing pagination evidence.

## Boundaries & Constraints

Use the existing paginated read projection and validate freshness against the checked PR; preserve rule 1 precedence.

</frozen-after-approval>

## Code Map

- `src/plotroom/adapters/github/pr_details.py` — Pin review-thread evidence to the read head and preserve existing pagination.
- `src/plotroom/core/merge_gate.py` — Return separate first reasons for incomplete and unresolved evidence.

## Tasks & Acceptance

- [x] `src/plotroom/adapters/github/pr_details.py` — Pin review-thread evidence to the read head and preserve existing pagination.
- [x] `src/plotroom/core/merge_gate.py` — Return separate first reasons for incomplete and unresolved evidence.
- [x] Add deterministic observations and acceptance cases under `tests/`.

Given a fully paginated fixture with more than 100 resolved threads, when rule 2 evaluates, then it passes. One unresolved thread refuses until a refreshed snapshot shows resolution. An incomplete or missing page refuses.

## Implementation Notes

- `GitHubReadClient.review_threads` is the existing cursor loop. Bind pagination to the PR head and verify a consistent count across pages against the number of unique IDs. A failed refresh retains earlier rows as incomplete.
- Use the existing GraphQL fixture in `tests/test_github_details.py` for a multi-page success, moved head, and inconsistent count.

## Plan Change Log

## Review Triage Log

- Bound complete review-thread observations to the PR projection version as well as its SHA. A close and reopen or a head that moves away and back now requires a new thread observation even when the checked SHA matches.

## Verification

- `uv run python -m pytest tests/test_merge_gate_rule2.py tests/test_github_details.py` — 19 passed. Fixtures cover a 101-thread observation and refresh, missing or malformed pagination, a moved head, a same-SHA reopen, retained incomplete observations, and Rule 1 precedence.
- Ruff lint, Ruff format check, and Pyright (Python 3.14) passed on the touched Python files.
- Built: https://github.com/mgd43b/plotroom/pull/152
