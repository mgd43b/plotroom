---
id: 3
type: story
title: "The clerk runs on plotroom's tools"
parent: epic-clerk-migration
covers: [R2]
after: [2, 4.5, 1.9, 3.6, 3.7, 6.3, 6.4]
hitl: true
risk: high
tracker_id: "110"
remote: "https://github.com/mgd43b/plotroom/issues/110"
tracker_status: backlog
---

# The clerk runs on plotroom's tools

## Description

A clerk goal and procedure for plotroom (coordination/goals/clerk-plotroom.txt): the clerk wakes with plotroom watch, executes MERGE items from merge_gate, and leaves triggers to plotroom; epic 6's checklists replace the clerk's 'any other open PR merges' trigger. A person copies the live ledger.md and triggers.md from the clerk's state folder, runs plotroom import, keeps a drain session running, and runs the clerk this way for a working day with the sweep scripts stopped.

## Requirements

Verify: For one working day the clerk merges only through MERGE items, every imported trigger whose condition held fired once and none fired twice, and the day's log shows no sweep.sh run and no missed or double merge.

## References

- parent — _bmad-output/initiative-orchestration-control-plane/tickets/epic-clerk-migration/epic-clerk-migration.md

## Notes

- Check beyond its criteria: a merge made by hand during the day is logged outside_gate and reaches the orchestrator.
