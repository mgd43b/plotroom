---
id: 6
type: story
title: "End-to-end suite over the epic's Done when"
parent: epic-clerk-migration
covers: [R1, R2]
after: [5]
hitl: false
risk: low
tracker_id: "113"
remote: "https://github.com/mgd43b/plotroom/issues/113"
tracker_status: backlog
---

# End-to-end suite over the epic's Done when

## Description

One suite runs the epic's import checks against a real daemon with fixture ledger and triggers files; the working-day check is recorded in the epic's Notes.

## Requirements

Verify: The suite passes in CI, one test per Done-when line except the working day, which is checked in the epic's Notes.

## References

- parent — _bmad-output/initiative-orchestration-control-plane/tickets/epic-clerk-migration/epic-clerk-migration.md
