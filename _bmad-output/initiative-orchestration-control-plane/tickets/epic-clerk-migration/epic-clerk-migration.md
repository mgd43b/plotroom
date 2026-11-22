---
tracker_id: "9"
remote: https://github.com/mgd43b/plotroom/issues/9
type: epic
title: "Clerk migration"
parent: initiative-orchestration-control-plane
covers: [R23]
sources: []
companions:
  - _bmad-output/initiative-orchestration-control-plane/architecture-orchestration-control-plane/architecture-orchestration-control-plane.md
after: [epic-triggers, epic-merge-controller]
refined: false
risk: low
---

# Clerk migration

## Description

The clerk's markdown ledger and triggers are imported through the server's commands, and the clerk runs its merge sweep and triggers on plotroom's MCP calls with no cutover day.

## Outcome

The clerk moves from markdown files onto plotroom without a cutover day.

## Done when

- `plotroom import` loads a sample ledger and triggers file through the server's commands, with fired triggers as fired and prose triggers as next items.
- The clerk session runs its sweep and triggers on plotroom's tools for a working day.

## Boundaries

- Out: changing the clerk's role.

## References

- parent — _bmad-output/initiative-orchestration-control-plane/initiative-orchestration-control-plane.md, Requirements R23
- architecture — _bmad-output/initiative-orchestration-control-plane/architecture-orchestration-control-plane/architecture-orchestration-control-plane.md

## Notes

- Waits on epic-triggers because: trigger records and fired state.
- Waits on epic-merge-controller because: MERGE next items and merge_gate for the clerk's sweep.
- Unknown: What the clerk's ledger.md holds (checklists, decisions or asks would add epics 6 and 7 as prerequisites); the clerk spike in epic 1 answers it.
