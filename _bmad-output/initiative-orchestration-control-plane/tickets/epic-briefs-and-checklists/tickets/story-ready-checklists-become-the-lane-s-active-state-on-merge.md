---
id: 2
type: story
title: "READY checklists become the lane's active state on merge"
parent: epic-briefs-and-checklists
covers: [R2]
after: [1.8, 2.15]
hitl: false
risk: medium
tracker_id: "96"
remote: "https://github.com/mgd43b/plotroom/issues/96"
tracker_status: done
---

# READY checklists become the lane's active state on merge

## Description

lane_ready(lane, pr, head_sha, checklist[]) stores the post-merge checklist as structured rows (step, owner, ref); when the projection shows the lane's PR merged, the rows become the lane's active state, shown in snapshot(). Hands on: checklist rows to entries 3 and 4 and to epic 9's import.

## Requirements

Verify: A lane's READY with three steps stores three rows; when its PR merges in the test repository they become its active state and snapshot() shows them; a READY with a free-text checklist is refused.

## References

- parent — _bmad-output/initiative-orchestration-control-plane/tickets/epic-briefs-and-checklists/epic-briefs-and-checklists.md
