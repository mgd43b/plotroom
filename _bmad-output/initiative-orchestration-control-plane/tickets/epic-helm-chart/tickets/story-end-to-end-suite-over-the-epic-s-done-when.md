---
id: 7
type: story
title: "End-to-end suite over the epic's Done when"
parent: epic-helm-chart
covers: [R1, R2]
after: [1]
hitl: false
risk: low
tracker_id: "127"
remote: "https://github.com/mgd43b/plotroom/issues/127"
tracker_status: done
---

# End-to-end suite over the epic's Done when

## Description

One suite installs the chart on kind and runs every Done-when check of this epic with MCP SDK clients configured as a local daemon's clients are; the real-client check is entry 3's.

## Requirements

Verify: The suite passes in CI, one test per Done-when line except the real clients, which are checked in entry 3.

## References

- parent — _bmad-output/initiative-orchestration-control-plane/tickets/epic-helm-chart/epic-helm-chart.md

## Notes

- Decision, 2026-11-26, Matt in the Plotroom coordinator chat: “go ahead and verify and close #127 using the passing local Helm evidence. keep publishing deferred.”
- Local CI acceptance on `731e07b2c72f06582124eac780a1a4ac6569a9e0`: the Helm job installed the chart on kind, verified one `Recreate` replica and a bound `ReadWriteOnce` claim, and received HTTP 200 from the project readiness endpoint. The authenticated SDK snapshot over `/mcp` and retained event after pod replacement passed. The outside-kind bridge round trip passed in the same job. [Exact Helm job](https://github.com/mgd43b/plotroom/actions/runs/45925376913/job/140265343705)
- The real Claude Code and Codex checks remain recorded separately in completed [#123](https://github.com/mgd43b/plotroom/issues/123). Local storage evidence is limited to the bound claim and persistence. Public OCI publication and hosted PV backing are outside this witness.
- #121 supplies the existing kind suite. The future #126 cleanup still requires passing Helm CI for its changes before its own completion.
