---
id: 8
type: story
title: "Listing and canceling triggers"
parent: epic-triggers
covers: [R5, R7]
after: [5]
hitl: false
risk: low
tracker_id: "54"
remote: "https://github.com/mgd43b/plotroom/issues/54"
tracker_status: backlog
---

# Listing and canceling triggers

## Description

Adds triggers(lane?) and cancel(trigger_id) (orchestrator only), canceled triggers never firing, and trigger counts by state in snapshot().

## Requirements

Verify: triggers() lists open, fired and canceled triggers with their bindings and triggers(lane) only that lane's; a canceled trigger whose predicate then holds does not fire; a clerk or worker token's cancel is refused; snapshot() shows the counts.

## References

- parent — _bmad-output/initiative-orchestration-control-plane/tickets/epic-triggers/epic-triggers.md
