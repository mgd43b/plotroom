---
id: 10
type: story
title: "End-to-end suite over the epic's Done when"
parent: epic-triggers
covers: [R1, R2, R3, R4, R5, R6, R7]
after: [9]
hitl: false
risk: low
tracker_id: "62"
remote: "https://github.com/mgd43b/plotroom/issues/62"
tracker_status: backlog
---

# End-to-end suite over the epic's Done when

## Description

One suite runs every Done-when check of this epic against a real daemon with the test repository, a scripted switchboard participant and entry 7's drain stand-in.

## Requirements

Verify: The suite passes in CI, one test per Done-when line.

## References

- parent — _bmad-output/initiative-orchestration-control-plane/tickets/epic-triggers/epic-triggers.md
