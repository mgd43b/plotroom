---
tracker_id: "5"
remote: https://github.com/mgd43b/plotroom/issues/5
type: epic
title: "Claims and overlaps"
parent: initiative-orchestration-control-plane
covers: [R13, R14]
sources: []
companions:
  - _bmad-output/initiative-orchestration-control-plane/architecture-orchestration-control-plane/architecture-orchestration-control-plane.md
after: []
refined: true
risk: medium
status: done
---

# Claims and overlaps

## Description

Lanes claim paths, every PR diff and ticket-tree move is checked against other lanes' claims and open PRs, and each overlap reaches next() before review.

## Outcome

File conflicts between lanes are found before review, not by hand after it.

## Requirements

- R1 (parent R13): Lanes claim and release path globs with a reason, and a claim or a PR diff that overlaps another lane's claim opens an item before review.
- R2 (parent R14): A PR that moves ticket-tree files other open PRs edit opens an item with a suggested order.
- R3 (parent R13): overlaps(pr | lane) and claims(lane?) return current conflicts and claims, and snapshot() counts them.
- R4 (parent R13, R22; decision 2026-11-22): A worker claims and releases for its own lane, and the orchestrator for any lane.

## Constraints

- From the initiative: C7, C11 (the tree stays the source of truth).
- From the spine: AD-8, AD-16 and AD-18 bind this epic's stories.

## Done when

- A PR whose diff touches another lane's claim raises an overlap item before review.
- A ticket-tree move of files that open PRs edit raises an item with a suggested order.
- A claim conflicting with another lane's claim is reported when it is made.
- `overlaps()` for a PR and for a lane, and `claims()`, return what the items show, and `snapshot()` counts them.
- A worker claims only for its own lane, and the orchestrator for any lane.

## Boundaries

- Out: briefs that print claims (epic 6).
- Owns claims, PR-diff overlap checks and tree-move checks; reads the ticket-tree mirror built in epics 1 and 2.

## References

- parent — _bmad-output/initiative-orchestration-control-plane/initiative-orchestration-control-plane.md, Requirements R13, R14
- architecture — _bmad-output/initiative-orchestration-control-plane/architecture-orchestration-control-plane/architecture-orchestration-control-plane.md

## Notes

- Waits on epic-server-and-ledger, story by story, for: workers and lanes.
- Waits on epic-one-inbox, story by story, for: changed files in the GitHub projection and the default-branch tree mirror.
- Decision: 2026-11-22, the owner chose who claims ('Lane + orchestrator'). The tracer bullet is entry 1; prerequisites are story by story on epics 1 and 2, with no whole-epic gate.
- Decision: 2026-11-22, build order 1, 2, 3, 4, with a refactor sweep (5) and an end-to-end suite (6); globs have no negation and claim overlaps use literal prefixes.
