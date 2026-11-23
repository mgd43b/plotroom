---
id: 7
type: story
title: "End-to-end suite over the epic's Done when"
parent: epic-helm-chart
covers: [R1, R2]
after: [6]
hitl: false
risk: low
tracker_id: "127"
remote: "https://github.com/mgd43b/plotroom/issues/127"
tracker_status: backlog
---

# End-to-end suite over the epic's Done when

## Description

One suite installs the chart on kind and runs every Done-when check of this epic with MCP SDK clients configured as a local daemon's clients are; the real-client check is entry 3's.

## Requirements

Verify: The suite passes in CI, one test per Done-when line except the real clients, which are checked in entry 3.

## References

- parent — _bmad-output/initiative-orchestration-control-plane/tickets/epic-helm-chart/epic-helm-chart.md
