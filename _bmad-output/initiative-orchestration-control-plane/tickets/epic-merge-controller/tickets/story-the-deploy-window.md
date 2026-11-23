---
id: 12
type: story
title: "The deploy window"
parent: epic-merge-controller
covers: [R4]
after: [2]
hitl: false
risk: low
tracker_id: "68"
remote: "https://github.com/mgd43b/plotroom/issues/68"
tracker_status: backlog
---

# The deploy window

## Description

freeze(reason) and unfreeze(), orchestrator only, set and clear the deploy window, during which every PR is refused with that reason.

## Requirements

Verify: During a freeze every PR is refused with its reason, unfreeze restores the earlier verdicts, and a clerk or worker token's freeze is refused.

## References

- parent — _bmad-output/initiative-orchestration-control-plane/tickets/epic-merge-controller/epic-merge-controller.md
