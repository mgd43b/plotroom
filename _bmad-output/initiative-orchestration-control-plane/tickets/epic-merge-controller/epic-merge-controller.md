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
after: [epic-one-inbox]
refined: false
risk: medium
---

# Merge controller

## Description

Every PR is judged by one gate over the GitHub projection, with freezes, flake candidates, automatic branch updates and a logged verdict per head SHA, and a MERGE verdict is executed by the orchestrator or clerk.

## Outcome

Merging follows fixed, logged rules on every event instead of a scratchpad sweep, and flakes no longer freeze the project.

## Done when

- `merge_gate(pr)` and the live gate return the same verdict on recorded projection fixtures covering all six rules.
- A MERGE verdict appears in `next()`, the clerk executes it, and it is logged as `gated`; a merge by hand is logged as `outside_gate`.
- A red main holds everything but the labeled fix, and a flake on main raises a flake item without freezing.

## Boundaries

- Out: executing merges directly (a per-project switch, built off).
- Owns the gate, freezes, flake candidates, rule-4 branch updates, the merge log and the one-open-PR rule.

## References

- parent — _bmad-output/initiative-orchestration-control-plane/initiative-orchestration-control-plane.md, Requirements R10, R11, R12, R25
- architecture — _bmad-output/initiative-orchestration-control-plane/architecture-orchestration-control-plane/architecture-orchestration-control-plane.md

## Notes

- Waits on epic-one-inbox because: the GitHub projection.
