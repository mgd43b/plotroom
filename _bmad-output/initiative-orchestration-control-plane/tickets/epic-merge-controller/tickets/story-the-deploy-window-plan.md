---
title: "Let the orchestrator control the deploy window"
type: feature
ticket: 12
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
  - _bmad-output/initiative-orchestration-control-plane/tickets/epic-merge-controller/tickets/story-the-deploy-window.md
---

<frozen-after-approval reason="human-owned intent">

## Intent

Persist a typed freeze reason and clear it through authorized project commands. The same gate query and fixture action intake honor the window.

## Boundaries & Constraints

Reuse the project’s serialized write path and command receipts. Keep the window local to the active project.

</frozen-after-approval>

## Code Map

- `src/plotroom/core/catalog.py`, `src/plotroom/core/ledger.py`, `src/plotroom/adapters/mcp/server.py` — Register typed orchestrator-only commands and use the same state when evaluating verdicts.
- `src/plotroom/adapters/store_sqlite/store.py`, `src/plotroom/ports/store.py` — Persist and consistently read the window alongside the projection.

## Tasks & Acceptance

- [x] `src/plotroom/core/catalog.py`, `src/plotroom/core/ledger.py`, `src/plotroom/adapters/mcp/server.py` — Register typed orchestrator-only commands and use the same state when evaluating verdicts.
- [x] `src/plotroom/adapters/store_sqlite/store.py`, `src/plotroom/ports/store.py` — Persist and consistently read the window alongside the projection.
- [x] Add deterministic observations and acceptance cases under `tests/`.

Given two PRs with different pre-freeze verdicts, when the orchestrator freezes and then unfreezes the project, then the freeze reason holds both PRs during the window and their earlier verdicts return afterward. Clerk and worker attempts are refused.

## Implementation Notes

- Store the current freeze reason under one reserved key in the existing project metadata table. Verify a populated upgrade and single head after integrating the claims lane.
- Use the normal tool request IDs and project command receipts. Read the freeze in the same SQLite snapshot as gate evidence, including on projects with no GitHub observations. Retain head and watermark if present when returning the freeze reason.
- Extend the exact MCP transport contract's tool lists and test restart persistence and worker/clerk denial.

## Plan Change Log

## Review Triage Log

- Medium, fixed. The active window is included in the budgeted board snapshot and is read alongside the rest of the summary.
- Medium, fixed. The Rule 4 fixture stages an update, freezes before dispatch, observes zero calls and then unfreezes and observes exactly one call.

## Verification

- `uv run python -m pytest tests/test_merge_freeze.py tests/test_branch_updates.py tests/test_transport_contract.py tests/test_migrations.py` — scoped fixture contracts pass.
- Deploy-window, board snapshot, transport and migration tests passed together (30). A fresh SQLite reader preserves the freeze; unfreeze restores a fixture MERGE and a check refusal. The transport contract exercises HTTP and stdio across both supported protocol eras.
- Built: https://github.com/mgd43b/plotroom/pull/152
