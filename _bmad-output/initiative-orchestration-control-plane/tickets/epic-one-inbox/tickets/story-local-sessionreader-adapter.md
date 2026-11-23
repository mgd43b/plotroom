---
id: 18
type: story
title: "Local SessionReader adapter"
parent: epic-one-inbox
covers: [R7]
after: [1, 9]
hitl: true
risk: medium
tracker_id: "44"
remote: "https://github.com/mgd43b/plotroom/issues/44"
tracker_status: backlog
---

# Local SessionReader adapter

## Description

Implements the SessionReader port with the mechanism entry 1 found, writing the same claude.* events as ingest so a turn seen both ways is stored once. Dropped, with a Dropped: line, if entry 1 finds no mechanism; dropping it also removes it from entry 12's after. A person runs a Claude desktop session on the machine for the check.

## Requirements

Verify: On a machine running the Claude desktop app, a lane's turn end appears once whether it arrives through the reader, through ingest with or without turn_index, or both, in either order.

## References

- parent — _bmad-output/initiative-orchestration-control-plane/tickets/epic-one-inbox/epic-one-inbox.md

## Notes

- Entry 1 found a mechanism (#31, PR #48): the three sources, the turn key (cliSessionId, turnIndex) and the reader's limits are in the epic's Notes.
