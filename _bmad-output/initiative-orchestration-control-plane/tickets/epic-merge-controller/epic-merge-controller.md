---
tracker_id: "4"
remote: https://github.com/mgd43b/plotroom/issues/4
type: epic
title: "Merge controller"
parent: initiative-orchestration-control-plane
covers: [R10, R11, R12, R25]
sources: []
companions:
  - _bmad-output/initiative-orchestration-control-plane/architecture-orchestration-control-plane/architecture-orchestration-control-plane.md
after: []
refined: true
risk: medium
---

# Merge controller

## Description

Every PR is judged by one gate over the GitHub projection, with freezes, flake candidates, automatic branch updates and a logged verdict per head SHA, and a MERGE verdict is executed by the orchestrator or clerk.

## Outcome

Merging follows fixed, logged rules on every event instead of a scratchpad sweep, and flakes no longer freeze the project.

## Requirements

- R1 (parent R10; spine AD-11; decision 2026-11-22): One gate function, with the clerk's safemerge.sh as its reference, gives the verdict for both merge_gate(pr) and live evaluation, with its reasons and the projection's as_of, logged per head SHA.
- R2 (parent R10; decision 2026-11-22): Rules 1 and 2 hold as in safemerge.sh: at least the configured minimum checks, none pending or failing outside the ignore list, no unresolved review thread, and anything unreadable is a refusal.
- R3 (parent R10): Rules 3 to 6 hold: main green or the marked fix, no file changed on main since the merge base (else one automatic branch update by merging main), no closing keyword in the body or commits, and every verdict pinned to the head SHA.
- R4 (parent R11; spine AD-16): Main red is derived from main's CI, a flake on main is not red, the marked fix is a configured label applied by a configured login, and freeze() and unfreeze() set the deploy window.
- R5 (parent R10, R22; decision 2026-11-22): A MERGE verdict is an item the orchestrator or clerk executes; an observed merge is logged gated or outside_gate; a refusal is an item the owning lane's worker sees through wait(); controller-run merging is a per-project setting, off by default.
- R6 (parent R12): A fail-then-pass on one head, or a failure outside the PR's changed files, opens a flake-candidate item with the test id and a log excerpt, and nothing is rerun.
- R7 (parent R25): A second open PR on a lane opens an item and is refused by the gate.

## Constraints

- From the initiative: C3 and C4 (the tool's only GitHub writes are branch updates by merging main and, when switched on, gated merges), C7.
- From the spine: AD-11, AD-14, AD-16 and AD-18 bind this epic's stories.

## Done when

- `merge_gate(pr)` and the live gate return the same verdict on recorded projection fixtures covering all six rules.
- A MERGE verdict appears in `next()`, the clerk executes it, and it is logged as `gated`; a merge by hand is logged as `outside_gate`.
- A red main holds everything but the labeled fix, and a flake on main raises a flake item without freezing.
- The gate's verdicts on the test repository's PRs match safemerge.sh's for the same PRs.

## Boundaries

- Out: turning controller-run merging on (entry 8 builds the setting, off).
- Owns the gate, freezes, flake candidates, rule-4 branch updates, the merge log and the one-open-PR rule.

## References

- parent — _bmad-output/initiative-orchestration-control-plane/initiative-orchestration-control-plane.md, Requirements R10, R11, R12, R25
- architecture — _bmad-output/initiative-orchestration-control-plane/architecture-orchestration-control-plane/architecture-orchestration-control-plane.md

## Notes

- Waits on epic-server-and-ledger, story by story, for: the action runner, workers and lanes, and wait().
- Waits on epic-one-inbox, story by story, for: the GitHub projection.
- Decision: 2026-11-22, the owner chose the clerk's safemerge.sh as the gate's reference ('Copy the clerk's gate') and refusals as items the lane's own wait() sees ('Item the lane's wait() sees').
- Decision: 2026-11-22, the tracer bullet is entry 1 (merge_gate on rules 1 and 2); prerequisites are story by story on epic 2, with no whole-epic gate.
- Decision: 2026-11-22, matching the clerk's gate settles the details: rule 4's branch update runs only when rules 1 to 3 pass, outside a red main or deploy window; allow-overlap and keywords-ok are labels from configured logins, like the fix label; pending and too-few-checks verdicts open no items, and refusals are deduplicated on PR, head and reason; canceled and skipped checks pass; drafts and PRs with the skip label are not gated, and a PR with no lane is gated with its refusals for the orchestrator.
- Decision: 2026-11-22, MERGE items are for the clerk (the orchestrator when no clerk is configured), and a labeled-fix MERGE for the orchestrator only, as the clerk skill has the coordinator merge the fix itself.
- Decision: 2026-11-22, build order: the gate chain 1, 11, 6, 2, 3 (the least certain), 12, 4, 13, then 5, 7 and 8, with a refactor sweep (9) and an end-to-end suite against safemerge.sh (10) closing the epic.
- Decision: 2026-11-23, the coordinator released the fixture-backed completions for #70, #71, #73, and #74 after [#155](https://github.com/mgd43b/plotroom/pull/155) and kept #67 open for its [scoped permissions and live branch-update check](https://github.com/mgd43b/plotroom/issues/67#issuecomment-7106227955). Separate prior owner authorization for those four fixture closures was not located. #67 retains the live re-check for the four outcomes. Live GitHub writes still require owner authorization and the dedicated test repository.
- Native acceptance dependency: #67 supplies the outstanding live Rule 4 branch-update evidence required by R3 on the authorized test repository.
