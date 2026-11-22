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
after: [epic-one-inbox]
refined: false
risk: medium
---

# Claims and overlaps

## Description

Lanes claim paths, every PR diff and ticket-tree move is checked against other lanes' claims and open PRs, and each overlap reaches next() before review.

## Outcome

File conflicts between lanes are found before review, not by hand after it.

## Done when

- A PR whose diff touches another lane's claim raises an overlap item before review.
- A ticket-tree move of files that open PRs edit raises an item with a suggested order.
- A claim conflicting with another lane's claim is reported when it is made.

## Boundaries

- Out: briefs that print claims (epic 6).
- Owns claims, PR-diff overlap checks and tree-move checks; reads the ticket-tree mirror built in epics 1 and 2.

## References

- parent — _bmad-output/initiative-orchestration-control-plane/initiative-orchestration-control-plane.md, Requirements R13, R14
- architecture — _bmad-output/initiative-orchestration-control-plane/architecture-orchestration-control-plane/architecture-orchestration-control-plane.md

## Notes

- Waits on epic-one-inbox because: changed files in the GitHub projection and the default-branch tree mirror.
