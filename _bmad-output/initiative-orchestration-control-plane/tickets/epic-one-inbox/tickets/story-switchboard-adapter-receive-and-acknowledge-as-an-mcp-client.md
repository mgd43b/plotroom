---
id: 7
type: story
title: "Switchboard adapter: receive and acknowledge as an MCP client"
parent: epic-one-inbox
covers: [R3, R4, R11]
after: [1.4, 1.5, 1.7, 1.8]
hitl: false
risk: high
tracker_id: "30"
remote: "https://github.com/mgd43b/plotroom/issues/30"
tracker_status: backlog
---

# Switchboard adapter: receive and acknowledge as an MCP client

## Description

In local mode plotroom spawns the switchboard's stdio MCP server, registers a protected name (plotroom) and blocks on its wait() in a loop, holding a participant with the switchboard unchanged; turns each claimed message into a switchboard.message event with its lane inferred from the sender's configured channel address, and acknowledges it upstream with that delivery's own receipt once the event commits, a duplicate included. It defines core's shared lane-inference hook that entry 15 extends. A scripted switchboard participant stands in for a Codex worker in tests. Hands on: the participant to entries 17 and 8, and switchboard events to entry 9.

## Requirements

Verify: The scripted participant's message to plotroom becomes one event on the sender's lane and is acknowledged once, and a redelivery after a crash before the ack is stored once and acknowledged.

## References

- parent — _bmad-output/initiative-orchestration-control-plane/tickets/epic-one-inbox/epic-one-inbox.md
- architecture-orchestration-control-plane/architecture-orchestration-control-plane.md#ad-10

## Notes

- Check beyond its criteria: a switchboard that is down at startup is retried with backoff, and nothing is lost when it comes back.
- Decision: 2026-11-22, settled from the switchboard's own README: spawn its stdio server, register a protected name, loop on wait() (it blocks up to its timeout and claims mail with receipts).
- Hands on from #15 (PRs #50, #92): event kinds and commands register in core/catalog.py build_catalog(); an event's source must equal its kind's prefix; a duplicate returns the stored event whatever its fields, only after role, kind and source pass; migration 0002 carries CHECK(seq > 0) and CHECK(json_valid(fields)), and the next revision's parent is 0002; Ledger.execute checks no role yet. An adapter's cursor write goes through UnitOfWork in the same transaction as its events.
- Hands on: switchboard.message events to epic 10's search tracer (entry 10.1).
