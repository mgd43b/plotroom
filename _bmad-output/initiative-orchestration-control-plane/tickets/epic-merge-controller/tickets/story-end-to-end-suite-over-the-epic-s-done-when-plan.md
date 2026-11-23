---
title: "End-to-end suite over the epic's Done when"
type: feature
ticket: '10'
created: '2026-11-23'
status: built
baseline_revision: 'b9bb69382b42edec2a4112113e278dade63a3dd8'
route: full
route_source: auto
risk: medium
review: thorough
review_source: pinned
lenses_ran: [native-safety, native-lifecycle, native-acceptance]
review_loop_iteration: 0
context: ['AGENTS.md']
---

<frozen-after-approval reason="human-owned intent">

## Intent

Verify all four Epic 4 Done-when lines with a real daemon and dedicated fixture repository.

## Boundaries & Constraints

Preserve guard order, the deploy freeze, exact head and main evidence, and durable action safety. Use injected local fixtures for write effects. Keep the clerk's production gate outside the change.

</frozen-after-approval>

## Code Map

- `../../../../../tests/test_merge_controller_e2e.py` — Cover dry/live six-rule parity, next and observed merge lifecycle, main red and flakes, and clerk shell parity.
- `../../../../../tests/fixtures/` — Use exact local shell read fixtures with ORCH_DRY=1. Protect against unexpected commands and writes.

## Tasks & Acceptance

- [x] `../../../../../tests/test_merge_controller_e2e.py` — Cover dry/live six-rule parity, next and observed merge lifecycle, main red and flakes, and clerk shell parity.
- [x] `../../../../../tests/fixtures/` — Use exact local shell read fixtures with ORCH_DRY=1. Protect against unexpected commands and writes.

- Given deterministic fixture PRs, when Epic 4 acceptance runs, then all four Done-when lines pass with zero real provider writes.

## Implementation Notes

- The real daemon uses authenticated role-specific clients and a local fixture repository. The shell comparison invokes the unchanged clerk gate with a restricted PATH and ORCH_DRY=1.

The four tests use the daemon's real GitHub read pollers, SQLite ledger, and authenticated MCP tools with a dedicated in-memory repository. The clerk consumes the pinned offer against that fixture and the poller records the observed merge. The acceptance suite verifies moved-head supersession, quiet waiting for checks, same-head refusal deduplication, and observation replay. It confirms that the pinned gate sees a new unresolved thread before the durable projection refreshes and that the offer retires after a freeze or projection refresh.

The shell comparison invokes the original clerk gate under `ORCH_DRY=1`. The command shims serve exact read responses and audit every GitHub and Git command, including unexpected commands inside shell pipelines.

## Plan Change Log

## Review Triage Log

- The first fixture checked the command then assigned the merge SHA directly. The local fixture executor now consumes the offered command and refuses a moved head.
- The main flake case could pass without relying on the proof. The otherwise identical case remains red without the explicit passing-test evidence.
- Added shell parity for the exact C-locale keyword boundary.

## Verification

- `uv run python -m pytest -q tests/test_merge_controller_e2e.py tests/test_merge_freeze.py` — all four Epic 4 acceptance checks and three freeze regressions passed on Python 3.14.
- `uv run python -m pytest -q tests` — acceptance and regression suite passes
- Built: https://github.com/mgd43b/plotroom/pull/155
