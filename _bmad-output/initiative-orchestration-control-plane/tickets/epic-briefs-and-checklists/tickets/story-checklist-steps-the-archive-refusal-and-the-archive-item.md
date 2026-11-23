---
id: 3
type: story
title: "Checklist steps, the archive refusal and the archive item"
parent: epic-briefs-and-checklists
covers: [R2, R3]
after: [2, 5.1]
hitl: false
risk: low
tracker_id: "97"
remote: "https://github.com/mgd43b/plotroom/issues/97"
tracker_status: done
---

# Checklist steps, the archive refusal and the archive item

## Description

checklist_done(lane, step) by the lane's worker or the orchestrator marks a step done with its ref; lane_archive is refused while the lane has open steps (alongside epic 5's claim release on archive); when the last step closes, or on merge when the READY checklist was empty, one state-backed archive item opens for the orchestrator, deduplicated per lane. This is the one archive item kind: epic 3's propose_archive opens the same item. Hands on: the item kind to epic 3 entry 6.

## Requirements

Verify: Archiving a lane with an open step is refused naming it; marking every step done opens one archive item, a propose_archive for the same lane opens no second one, and archiving the lane closes it; a lane whose READY had no steps gets the item when its PR merges; another lane's worker cannot mark the step.

## References

- parent — _bmad-output/initiative-orchestration-control-plane/tickets/epic-briefs-and-checklists/epic-briefs-and-checklists.md
