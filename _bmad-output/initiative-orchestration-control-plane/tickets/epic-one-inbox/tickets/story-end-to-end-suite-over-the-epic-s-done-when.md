---
id: 13
type: story
title: "End-to-end suite over the epic's Done when"
parent: epic-one-inbox
covers: [R1, R2, R3, R4, R5, R6, R7, R8, R9, R10, R11]
after: [12]
hitl: false
risk: low
tracker_id: "46"
remote: "https://github.com/mgd43b/plotroom/issues/46"
tracker_status: backlog
---

# End-to-end suite over the epic's Done when

## Description

One suite runs every Done-when check of this epic against the test repository, a scripted switchboard participant and the compose stack, and runs in CI with the secrets entries 2 and 6 added.

## Requirements

Verify: The suite passes against a local daemon and the compose stack, one test per Done-when line except the spike's answer, which is checked in the epic's Notes.

## References

- parent — _bmad-output/initiative-orchestration-control-plane/tickets/epic-one-inbox/epic-one-inbox.md
