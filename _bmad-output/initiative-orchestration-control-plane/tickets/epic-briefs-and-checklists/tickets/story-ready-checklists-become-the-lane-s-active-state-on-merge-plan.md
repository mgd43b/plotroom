---
title: 'Store and activate a structured READY checklist'
type: feature
ticket: 2
created: '2026-11-23'
status: in-progress
baseline_revision: '65ed0835eeab89276f197f375cc1ebdcd87dfc54'
route: full
route_source: auto
risk: medium
context: [AGENTS.md]
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

Store and activate a structured READY checklist as part of the local Epic 6 lane.

## Boundaries & Constraints

Follow the canonical story and Epic 6 Done-when criteria. Use local fixture evidence and the existing role, ledger and projection rules.

## Acceptance Criteria

Given a READY with three typed step/owner/ref rows, when GitHub reports the same repository, branch, PR and head merged, then the persisted rows become active once and the snapshot retains their PR and head. Given a different or stale PR observation, when it is processed, then it cannot activate the checklist. Given free text, when the tool is called, then validation refuses it.

</frozen-after-approval>

## Code Map and Tasks

- [x] `core/lanes.py`, `core/github.py` and `core/checklists.py` own READY and activation. Preserve existing omitted-checklist and internal mark calls. Capture a monotonic READY version and authoritative identity. Do not replace an active checklist. A nonmatching projection invalidates the pending READY without reusing its generation. Do not infer the merge from the disappearance of an open PR.
- [ ] `ports/store.py`, `core/unit_of_work.py`, `adapters/store_sqlite/ledger_sql.py`, `adapters/store_sqlite/checklists_sql.py` and `migrations/versions/0017_lane_checklists.py` store typed rows transactionally with a checklist header. Create the migration against the development base, then adjust the unpublished revision to the landed parent before final verification.
- [x] `adapters/mcp/server.py`, `core/catalog.py` and `adapters/store_sqlite/store.py` handle the typed list, writer rule and snapshot. Render only current pending/active state and retain active identity across later projection changes.
- [x] `tests/fakes.py` implements the new transaction methods for the existing in-memory ledger tests. `tests/test_migrations.py` keeps predecessor fixtures on their explicit revisions and fixes head assertions to check the actual head instead of a historic constant.
- [x] `tests/test_checklists.py` proves three stored rows, immutable active snapshot, wrong identity refusal, repeated READY and merge idempotency, and invalid input. Preserve the existing every-tool transport tests.

## Implementation Notes

Use the single existing lane row and a separate checklist header with positive version, PR, head, repository, and lifecycle. Store ordered step rows with step, owner, the declared ref, and nullable completion ref. Expose `snapshot(lane=...)` with `checklist: {version, pr, head_sha, state, steps}`, where states are `pending` and `active`. Have a single domain module for checklist behavior so completion and triggers can extend it. Keep no-checklist lanes' snapshot format and lifecycle intact.

## Plan Change Log

- 2026-11-23. Added populated-upgrade coverage and explicit READY reconciliation when current lanes share a branch. The named lane snapshot retains the active checklist's original PR and head. Migration `0017` uses local predecessor `0015` pending integration with the landed peer revision.

## Review Triage Log

## Verification

- `rtk proxy uv run --locked python -m pytest tests/test_checklists.py tests/test_github.py tests/test_lanes.py tests/test_migrations.py tests/test_transport_contract.py`
- Local run on Python 3.14.10 passed all 51 tests, including fixture HTTP and stdio, with single Alembic head `0017`. Ruff lint, formatting, and Pyright passed.
