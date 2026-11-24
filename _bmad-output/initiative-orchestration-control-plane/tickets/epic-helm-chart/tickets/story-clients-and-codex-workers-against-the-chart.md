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
tracker_status: backlog
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
- Chart wiring and scripted outside-kind acceptance were verified locally. Separate actual
  Claude Code and Codex acceptance is pending on the merged revision.
