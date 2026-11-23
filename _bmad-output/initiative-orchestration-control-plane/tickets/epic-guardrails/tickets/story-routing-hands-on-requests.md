---
id: 5
type: story
title: "Routing hands-on requests"
parent: epic-guardrails
covers: [R3]
after: [1.6, 1.8]
hitl: false
risk: low
tracker_id: "105"
remote: "https://github.com/mgd43b/plotroom/issues/105"
tracker_status: backlog
---

# Routing hands-on requests

## Description

route(action_kind, lane) (orchestrator only) records which lane owns a kind of hands-on action, config seeding a route only when the ledger has none; route_request(action_kind) returns the owning lane, and an unrouted request opens an item for the orchestrator.

## Requirements

Verify: A routed kind returns its lane, a config seed applies only on an empty routes table and never over a ledger route, an unrouted kind opens one item, and a worker token's route is refused.

## References

- parent — _bmad-output/initiative-orchestration-control-plane/tickets/epic-guardrails/epic-guardrails.md
