---
tracker_id: "7"
remote: https://github.com/mgd43b/plotroom/issues/7
type: epic
title: "The human's queue"
parent: initiative-orchestration-control-plane
covers: [R17, R18, R19]
sources: []
companions:
  - _bmad-output/initiative-orchestration-control-plane/architecture-orchestration-control-plane/architecture-orchestration-control-plane.md
after: [epic-one-inbox]
refined: false
risk: low
---

# The human's queue

## Description

Decisions are recorded in the human's exact words, asks on GitHub are flagged when buried, stale or unanswered, and goal() reports what is left and why.

## Outcome

The human's decisions are kept in their own words, and their asks stop getting buried.

## Done when

- `decide()` stores words byte for byte and rejects words not found in the cited GitHub comment.
- A buried, optionless, stale or orphaned ask is flagged in `next()`.
- `goal()` reports what is left of an encoded goal and why.

## Boundaries

- Out: making decisions (C1).

## References

- parent — _bmad-output/initiative-orchestration-control-plane/initiative-orchestration-control-plane.md, Requirements R17, R18, R19
- architecture — _bmad-output/initiative-orchestration-control-plane/architecture-orchestration-control-plane/architecture-orchestration-control-plane.md

## Notes

- Waits on epic-one-inbox because: issues and comments in the GitHub projection.
