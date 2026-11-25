---
id: 3
type: story
title: "Clients and Codex workers against the chart"
parent: epic-helm-chart
covers: [R2]
after: [1, 2.8]
hitl: true
risk: medium
tracker_id: "123"
remote: "https://github.com/mgd43b/plotroom/issues/123"
tracker_status: done
---

# Clients and Codex workers against the chart

## Description

The chart's documentation and values configure a hosted instance for plotroom bridge (a bridge token from the Job, the allowed Host names); a test runs the bridge from outside the cluster against the kind install; a person points a real Claude Code session at the chart's instance and a real Codex worker at it through the bridge.

## Requirements

Verify: A bridge run outside the kind cluster delivers a scripted switchboard participant's message to the hosted instance and a send back, once each, and a real Claude Code session and a real Codex worker each call snapshot() against the chart's instance.

## References

- parent — _bmad-output/initiative-orchestration-control-plane/tickets/epic-helm-chart/epic-helm-chart.md

## Notes

- Built: https://github.com/mgd43b/plotroom/pull/187
- Real-client acceptance on 2026-11-24 at main `4e0ce93f`. The local kind chart accepted the configured Host and returned HTTP 421 for a wrong Host or port. The scoped bridge credential was issued through a private Job with empty logs.
- An earlier interrupted probe verified inbound deduplication and left the outbound lease pending. In a fresh, uninterrupted fixture, an actual bridge outside kind and a scripted participant produced one inbound event and acknowledgment and one outbound relay delivery. The outbound action key matched and the lease completed.
- Claude Code 2.1.323 called `snapshot()` directly against the chart. Codex CLI 0.169.0 called `snapshot()` against the same chart through `plotroom connect --stdio`. Both returned `ok` using their existing sign-ins. The fixtures were cleaned up after the completed check.
- Combined real Codex bridge acceptance on 2026-11-24 at main `4e0ce93f`. In an isolated Switchboard Relay 0.2.12 fixture, a real Codex CLI 0.169.0 worker sent one message through the actual host bridge to the chart. The chart recorded one incoming event and one completed outgoing action. Its action key matched the single relay send. The same worker received the chart's reply through the bridge and acknowledged that receipt. The reply supplied the lane identifier. The worker then used authenticated `plotroom connect --stdio` to call `snapshot()` for that lane on the same chart daemon. The result was `ok` with the expected worker. This combined check is separate from the earlier Claude Code snapshot and scripted bridge round trip.
- An agent performed the independent real Claude Code snapshot and combined Codex bridge and snapshot check. [Merged evidence PR #188](https://github.com/mgd43b/plotroom/pull/188). Matt will decide whether to accept this evidence or repeat the check by hand.
