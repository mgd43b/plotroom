---
title: 'One open pr per lane'
type: feature
ticket: '7'
created: '2026-11-23'
status: in-review
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

Refuse a second open PR against its lane’s projected branch identity.

## Boundaries & Constraints

Preserve guard order, the deploy freeze, exact head and main evidence, and durable action safety. Use injected local fixtures for write effects. Keep the clerk's production gate outside the change.

</frozen-after-approval>

## Code Map

- `src/plotroom/core/lanes.py` — Accept an optional case-sensitive branch glob with exact branch fallback. Preserve existing lane ready semantics.
- `src/plotroom/core/merge_lane.py` — Resolve a unique non-archived lane on same-repository projected branches. Choose the lowest open matching PR number.
- `src/plotroom/adapters/store_sqlite/migrations/versions/0016_merge_controller.py` — Add a nullable branch pattern and preserve populated upgrades.
- `src/plotroom/core/merge_gate.py` — Apply duplicate refusal after established rules and independently prevent branch updates on a duplicate.

## Tasks & Acceptance

- [x] `src/plotroom/core/lanes.py` — Accept an optional case-sensitive branch glob with exact branch fallback. Preserve existing lane ready semantics.
- [x] `src/plotroom/core/merge_lane.py` — Resolve a unique non-archived lane on same-repository projected branches. Choose the lowest open matching PR number.
- [x] `src/plotroom/adapters/store_sqlite/migrations/versions/0016_merge_controller.py` — Add a nullable branch pattern and preserve populated upgrades.
- [x] `src/plotroom/core/merge_gate.py` — Apply duplicate refusal after established rules and independently prevent branch updates on a duplicate.

- Given two open same-lane PRs, when evaluated, then the later PR is refused and its item names the first. Closing the first allows reevaluation.

## Implementation Notes

- The merge view resolves active lane globs from same-repository projected branch identity, while existing lane ready and board behavior retains its exact-branch lookup.

- `lane_start` persists the optional `branch_pattern`. Merge gate snapshots read lane and PR projections in the same transaction. The gate identifies the earliest matching open PR by number.
- The fixture branch-update runner checks duplicate evidence before writing, including when an older PR opens after an action was queued.
- Migration tests seed historical projection rows using their original schema. The populated predecessor test checks that a ready lane keeps its exact branch, pinned PR, and head through the upgrade.

## Plan Change Log

## Review Triage Log

- A branch update could bypass lane ambiguity when Rule 4 refused first. The branch update guard now checks ambiguity directly.

## Integration

- Migration 0016 follows 0015 from merged PR #154. A populated predecessor upgrade preserves the lane and PR, and a separate upgrade preserves persisted trigger state.

## Verification

- `uv run python -m pytest -q tests` — acceptance and regression suite passes
