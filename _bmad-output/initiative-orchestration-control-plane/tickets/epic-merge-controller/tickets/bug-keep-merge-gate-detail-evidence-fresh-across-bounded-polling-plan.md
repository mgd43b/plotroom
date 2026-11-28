---
title: 'Keep merge gate detail evidence fresh across bounded polling'
type: bugfix
ticket: 18
created: '2026-11-27'
status: built
baseline_revision: 'c2e8a1bb04d42a1d8d0110450404cbce223d2835'
route: full
route_source: pinned
risk: high
review: quick
review_source: pinned
lenses_ran: [quick]
review_loop_iteration: 0
context: []
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

Keep required PR detail evidence fresh for seventeen healthy active PRs under the existing two-interval age limit. Bound provider work per cycle, give terminal enrichment its own fair allowance, and explicitly refuse when the known active population exceeds supported capacity.

## Boundaries & Constraints

Preserve evidence timestamps, repository, head and pull-version binding, cooldown, and the existing bounded pinned recheck. Unsupported capacity and missing, stale, or unreadable evidence fail closed. No live operations, credential changes, or activation.

## I/O & Edge-Case Matrix

| Evidence | Expected behavior |
| --- | --- |
| Seventeen open PRs, 10 second ticks plus healthy read time, 20 second window | Every active PR stays fresh after warm-up |
| Eighteen or more complete open PRs | Explicit capacity refusal with bounded fair sampling |
| Terminal backlog | Reserved bounded progress together with active refresh |
| Failed read, cooldown, restart, or head movement | Failure remains unusable, cooldown performs no work, restart resumes fairly, and old binding cannot bless the new PR revision |

</frozen-after-approval>

## Code Map

- `adapters/store_sqlite/github_sql.py` has the mixed oldest-first query capped at eight rows. Use separate limits of 17 active and 2 pending terminal requests per cycle. Persist and order by actual attempt time, with deterministic ties. Keep the query bounded and isolate terminal retries from active capacity.
- `adapters/github/details.py` applies evidence and persists attempt cursors. Record attempts after all relevant detail operations, including pull text, and only when the transaction still sees the exact repository, head, version and updated_at attempted. Error attempts retain their place in rotation without making evidence fresh or recording terminal completion. Preserve cooldown and the independent optional Kodus budget. Include required legacy statuses when deciding active readiness through the existing Rule 1 resources.
- `core/merge_gate.py`, `core/unit_of_work.py`, `ports/store.py`, and SQLite transaction storage provide a consistent snapshot. Derive project capacity overload from a bounded database probe of complete open PRs, not a stale in-memory counter. Emit a clear shared refusal over capacity and ensure already-issued live offers retract when the projection grows. Keep the fresh pinned provider read and expiry rules intact.
- `tests/` should mirror selector semantics in the fake and test real SQLite in the daemon or poller. Existing fixed-clock daemon fixtures can expose a mutable clock. Include nonzero healthy work between ten-second sleeps. A two-batch solution has no margin because `GitHubPoller.run` sleeps after each cycle.

## Tasks & Acceptance

- [x] `adapters/store_sqlite/`, `ports/store.py`, `core/merge_gate.py`, `core/unit_of_work.py` — Define one shared active budget, read capacity consistently, and expose the refusal.
- [x] `adapters/github/details.py` — Bring scheduling and durable attempt accounting into alignment with correct evidence, tested across restart and cooldown.
- [x] `tests/`, `docs/features.md` — Prove each matrix row using bounded provider fixtures and document the 17-active and 2-terminal limits. The scheduler sleeps after each pass. For a worst-case timing guarantee, each full healthy pass must fit within half the configured polling interval. This keeps the previous pass tail, sleep, and next pass prefix within the two-interval age. Actual delays still expire evidence. The separate optional Kodus budget continues to apply where configured.

Given a previously ready project, when the known active count crosses the capacity limit, then stale MERGE offers disappear and an advisory recheck refuses. Given capacity recovers, when correctly pinned resources refresh, then normal gate rules decide again.

## Implementation Notes

- The SQLite selector reserves 17 open and two terminal requests. Scope-bound attempt cursors retain fairness through restart and cooldown. The fixed-clock provider fixture passed the 17-active, 18-overload, and recovery cycle within the original freshness limit. The harness uses a separate bounded wall-clock wait for the expanded fixture.

## Plan Change Log

## Review Triage Log

- Medium, patch. The merge capacity refusal masked the existing Rule 4 branch-update decision. Keep capacity enforcement on MERGE and preserve the branch-update eligibility checks.
- Medium, patch. A recurring rate limit during the checks phase could abort every pass before any healthy detail reads. Preserve complete per-head progress before the next provider request while keeping immediate cooldown.
- High, patched under 4.17. The status paginator discarded a known missing last page. Both reviewers reproduced the same evidence gap by inspection.

## Verification

Built: [PR #234](https://github.com/mgd43b/plotroom/pull/234).

- Locked Python 3.15.2 full suite passed all 1,532 tests.
- Locked Python 3.14.10 full run passed 1,531 tests. The mounted-PAT smoke test exposed a preexisting snapshot/readiness race that was independently reproduced on the baseline tree. The exact test passed in a separate diagnostic run on the same candidate.
- Ruff lint and formatting, Pyright, and the core dependency check passed on both interpreters.
- Independent reviewers confirmed the fixes for every in-scope finding.
