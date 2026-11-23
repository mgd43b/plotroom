---
id: 8
type: story
title: "plotroom bridge for a hosted instance"
parent: epic-one-inbox
covers: [R5, R3, R4]
after: [7, 17, 1.9, 1.10]
hitl: false
risk: medium
tracker_id: "42"
remote: "https://github.com/mgd43b/plotroom/issues/42"
tracker_status: backlog
---

# plotroom bridge for a hosted instance

## Description

plotroom bridge runs on the switchboard's machine with entries 7 and 17's participant and a bridge token: it appends switchboard.* events to the hosted instance, leases and completes the hosted instance's pending switchboard sends, wakes through wait(), and acknowledges each receipt upstream once the hosted event commits.

## Requirements

Verify: With the compose stack as the hosted instance, the scripted participant's message reaches it through the bridge once and is acknowledged, a hosted send reaches the participant, and a bridge killed between the hosted commit and the upstream ack leaves the redelivered message stored once.

## References

- parent — _bmad-output/initiative-orchestration-control-plane/tickets/epic-one-inbox/epic-one-inbox.md
- architecture-orchestration-control-plane/architecture-orchestration-control-plane.md#ad-10

## Notes

- Real clients seen by entry 1.3 (lane #14): Claude Code 2.1.323 speaks the 2026 era (server/discover, a long-lived subscriptions/listen) and Codex 0.169 the 2025-06-18 handshake era; both send notifications/cancelled. The bridge's HTTP calls and the Codex side must work with both.
