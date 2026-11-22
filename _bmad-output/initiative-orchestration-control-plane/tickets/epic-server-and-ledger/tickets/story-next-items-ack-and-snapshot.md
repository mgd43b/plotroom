---
id: 6
type: story
title: "Next items, ack and snapshot()"
parent: epic-server-and-ledger
covers: [R2, R3, R6]
after: [4, 5]
hitl: false
risk: medium
tracker_id: "17"
remote: "https://github.com/mgd43b/plotroom/issues/17"
tracker_status: backlog
---

# Next items, ack and snapshot()

## Description

Adds the next-item store in AD-18's shape with its kind registry, dedup keys and a new seq on every change; next() ranked by rank_class; ack declared in the writer table, closing event-backed and snoozing state-backed items, a second ack a no-op; snapshot() summarizing the whole board within its budget through one estimate_tokens function, replacing entry 2's empty one. Later entries and epics only append item kinds, summary counts and result fields.

## Requirements

Verify: With fixture items, next() returns them in rank order, ack closes or snoozes each by kind and a second ack changes nothing, and snapshot() stays within budget and states how many items it left out.

## References

- parent — _bmad-output/initiative-orchestration-control-plane/tickets/epic-server-and-ledger/epic-server-and-ledger.md
