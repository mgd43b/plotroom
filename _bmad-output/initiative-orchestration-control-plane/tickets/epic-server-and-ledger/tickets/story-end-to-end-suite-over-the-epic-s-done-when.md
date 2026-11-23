---
id: 16
type: story
title: "End-to-end suite over the epic's Done when"
parent: epic-server-and-ledger
covers: [R1, R2, R3, R4, R5, R6, R7, R8, R9, R11]
after: [12, 10]
hitl: false
risk: low
tracker_id: "27"
remote: "https://github.com/mgd43b/plotroom/issues/27"
tracker_status: backlog
---

# End-to-end suite over the epic's Done when

## Description

One suite runs every Done-when check of this epic against a real daemon, locally and in the container, and runs in CI.

## Requirements

Verify: The suite passes in CI against a local daemon and the compose stack, one test per Done-when line.

## References

- parent — _bmad-output/initiative-orchestration-control-plane/tickets/epic-server-and-ledger/epic-server-and-ledger.md
