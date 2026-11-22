---
tracker_id: "6"
remote: https://github.com/mgd43b/plotroom/issues/6
type: epic
title: "Briefs and checklists"
parent: initiative-orchestration-control-plane
covers: [R15, R16]
sources: []
companions:
  - _bmad-output/initiative-orchestration-control-plane/architecture-orchestration-control-plane/architecture-orchestration-control-plane.md
after: [epic-claims-and-overlaps, epic-triggers]
refined: false
risk: low
---

# Briefs and checklists

## Description

brief() fills a lane's brief with scope, claims to avoid and standing rules, and a READY lane's post-merge checklist becomes its active state until empty, when a checklist_empty trigger proposes the archive.

## Outcome

Every lane starts from a complete brief and ends with its follow-through tracked, so finished lanes get archived.

## Done when

- `brief()` returns a brief with a don't-touch section built from current claims and the standing rules.
- A lane's READY checklist becomes its active state on merge, archiving is refused while it has steps, and an empty checklist proposes the archive.

## Boundaries

- Out: claims themselves (epic 5) and the trigger engine (epic 3).
- Owns checklist rows and the `checklist_empty` predicate leaf.

## References

- parent — _bmad-output/initiative-orchestration-control-plane/initiative-orchestration-control-plane.md, Requirements R15, R16
- architecture — _bmad-output/initiative-orchestration-control-plane/architecture-orchestration-control-plane/architecture-orchestration-control-plane.md

## Notes

- Waits on epic-claims-and-overlaps because: current claims for the don't-touch section.
- Waits on epic-triggers because: the trigger engine and the propose_archive action.
