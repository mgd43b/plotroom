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
after: []
refined: true
risk: low
---

# Clerk migration

## Description

The clerk's markdown ledger and triggers are imported through the server's commands, and the clerk runs its merge sweep and triggers on plotroom's MCP calls with no cutover day.

## Outcome

The clerk moves from markdown files onto plotroom without a cutover day.

## Requirements

- R1 (parent R23; spine AD-3, AD-15): plotroom import loads the clerk's triggers.md and ledger.md through the running server's commands as orchestrator, fired triggers as fired, prose triggers as items to bind, and a second import changes nothing.
- R2 (parent R23): The clerk runs its merges and triggers on plotroom's tools for a working day, after which the hand-run clerk is retired from the repository.

## Constraints

- From the initiative: C1 (the clerk decides nothing), C7.
- From the spine: AD-3, AD-8 and AD-15 bind this epic's stories.

## Done when

- `plotroom import` loads a sample ledger and triggers file through the server's commands, with fired triggers as fired and prose triggers as next items.
- The clerk session runs its sweep and triggers on plotroom's tools for a working day.

## Boundaries

- Out: changing the clerk's role.

## References

- parent — _bmad-output/initiative-orchestration-control-plane/initiative-orchestration-control-plane.md, Requirements R23
- architecture — _bmad-output/initiative-orchestration-control-plane/architecture-orchestration-control-plane/architecture-orchestration-control-plane.md

## Notes

- Waits on epic-server-and-ledger, story by story, for: the writer-rule table, lanes and wait().
- Waits on epic-briefs-and-checklists, story by story, for: checklist rows for imported post-merge steps.
- Waits on epic-triggers, story by story, for: trigger records and fired state.
- Waits on epic-merge-controller, story by story, for: MERGE next items and merge_gate for the clerk's sweep.
- Unknown: What the clerk's ledger.md holds (checklists, decisions or asks would add epics 6 and 7 as prerequisites); the clerk spike in epic 1 answers it.
- Decision: 2026-11-22, build order 1 (triggers import, the tracer), 2 (ledger import, waiting on the clerk spike for what live ledgers hold), 3 (a person runs the clerk on plotroom for a working day), 4 (retire the hand-run clerk), with a refactor sweep (5) and an end-to-end suite (6). Prerequisites are story by story, with no whole-epic gate.
