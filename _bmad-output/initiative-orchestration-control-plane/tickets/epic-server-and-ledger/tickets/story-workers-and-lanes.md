---
id: 8
type: story
title: "Workers and lanes"
parent: epic-server-and-ledger
covers: [R3, R4, R7, R9]
after: [5, 6]
hitl: false
risk: medium
tracker_id: "19"
remote: "https://github.com/mgd43b/plotroom/issues/19"
tracker_status: backlog
---

# Workers and lanes

## Description

Workers come from config; lane_start, lane_update, lane_ready (lane, PR, head SHA; epic 6 adds checklists, epic 2 checks the arguments against the projection) and lane_archive (epic 6 adds the checklist refusal) record lanes with work items, branch, state and waiting_on under the writer rules; a lane waiting on the orchestrator raises an item; snapshot() gains lane counts.

## Requirements

Verify: Through both HTTP and the stdio proxy, an orchestrator token starts a lane for a configured worker and a worker token is refused lane_start; a lane_update waiting on the orchestrator raises an item; after a server restart, snapshot() and next() return the same board.

## References

- parent — _bmad-output/initiative-orchestration-control-plane/tickets/epic-server-and-ledger/epic-server-and-ledger.md
