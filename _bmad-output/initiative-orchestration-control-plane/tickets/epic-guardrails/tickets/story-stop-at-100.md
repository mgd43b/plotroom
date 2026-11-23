---
id: 2
type: story
title: "Stop at 100%"
parent: epic-guardrails
covers: [R2]
after: [1, 3.6, 3.7]
hitl: false
risk: medium
tracker_id: "103"
remote: "https://github.com/mgd43b/plotroom/issues/103"
tracker_status: backlog
---

# Stop at 100%

## Description

At 100% of its cap, a budget sends stop to its run's lane once over the lane's channel through epic 3's send, keyed budget:<bud_id>:100, and opens an item for the orchestrator.

## Requirements

Verify: A reading at the cap sends one stop to a Codex lane through the scripted switchboard participant and one to a Claude lane through the drain stand-in, each with its budget key, also across a restart.

## References

- parent — _bmad-output/initiative-orchestration-control-plane/tickets/epic-guardrails/epic-guardrails.md
