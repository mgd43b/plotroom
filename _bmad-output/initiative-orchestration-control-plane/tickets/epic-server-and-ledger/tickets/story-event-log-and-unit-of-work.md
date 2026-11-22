---
id: 4
type: story
title: "Event log and unit of work"
parent: epic-server-and-ledger
covers: [R1]
after: [2]
hitl: false
risk: high
tracker_id: "15"
remote: "https://github.com/mgd43b/plotroom/issues/15"
tracker_status: backlog
---

# Event log and unit of work

## Description

Adds the event-kind registry with typed fields and appending roles, events unique on (source, kind, upstream_id), the per-project writer task on one connection, one transaction per input, request_id deduplication of mutating commands recorded as core.<command> events, and the project seq. Hands on: the event log and registry to epic 2.

## Requirements

Verify: With a fixture command registered in the test suite, replaying a recorded input set twice leaves the ledger unchanged, and the command retried with the same request_id returns the first result.

## References

- parent — _bmad-output/initiative-orchestration-control-plane/tickets/epic-server-and-ledger/epic-server-and-ledger.md
- architecture-orchestration-control-plane/architecture-orchestration-control-plane.md#ad-2
- architecture-orchestration-control-plane/architecture-orchestration-control-plane.md#ad-14

## Notes

- Check beyond its criteria: killing the server mid-transaction leaves no partial input in the file.
