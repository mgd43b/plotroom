---
id: 4
type: story
title: "The checklist_empty trigger leaf"
parent: epic-briefs-and-checklists
covers: [R3]
after: [3, 3.1, 3.6]
hitl: false
risk: low
tracker_id: "98"
remote: "https://github.com/mgd43b/plotroom/issues/98"
tracker_status: done
---

# The checklist_empty trigger leaf

## Description

Adds checklist_empty(lane) to epic 3's leaf registry: it holds when the lane has an active checklist whose last step has closed (never for a lane that has not reported READY), so the orchestrator can trigger a send or propose_archive on it.

## Requirements

Verify: A trigger on checklist_empty(lane) fires once when the lane's last step closes, also across a restart, and does not fire at creation for a lane with no active checklist.

## References

- parent — _bmad-output/initiative-orchestration-control-plane/tickets/epic-briefs-and-checklists/epic-briefs-and-checklists.md
