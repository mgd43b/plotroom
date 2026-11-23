---
id: 7
type: story
title: "One open PR per lane"
parent: epic-merge-controller
covers: [R7]
after: [13, 2.15, 1.8]
hitl: false
risk: low
tracker_id: "72"
remote: "https://github.com/mgd43b/plotroom/issues/72"
tracker_status: backlog
---

# One open PR per lane

## Description

Lanes gain an optional branch pattern (a lane's branch when unset); a second open PR matching a lane's pattern opens an item for the orchestrator, and the gate refuses it until the first closes.

## Requirements

Verify: Opening a second PR on a lane's branch pattern opens one item and its verdict is a refusal naming the first PR; closing the first lets the second be judged.

## References

- parent — _bmad-output/initiative-orchestration-control-plane/tickets/epic-merge-controller/epic-merge-controller.md
