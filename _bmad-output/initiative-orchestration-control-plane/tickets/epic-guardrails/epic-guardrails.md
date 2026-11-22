---
tracker_id: "8"
remote: https://github.com/mgd43b/plotroom/issues/8
type: epic
title: "Guardrails"
parent: initiative-orchestration-control-plane
covers: [R7, R20, R21]
sources: []
companions:
  - _bmad-output/initiative-orchestration-control-plane/architecture-orchestration-control-plane/architecture-orchestration-control-plane.md
after: [epic-triggers]
refined: false
risk: low
---

# Guardrails

## Description

A usage adapter reads provider spend; paid runs carry hard caps that raise next() items at 50% and 80% and stop the lane at 100%; and hands-on requests are routed to their owning lane.

## Outcome

Paid runs cannot overrun their caps unnoticed, and hands-on work is routed away from the orchestrator.

## Done when

- A run's recorded spend raises items at 50% and 80% and sends stop at 100%, each once.
- A hands-on request returns its routed lane, and an unrouted one becomes a next item.

## Boundaries

- Out: holding provider keys (C2).
- Owns the `UsageMeter` adapter (its share of R7), budgets, the `budget_ratio` predicate leaf and the routes table.

## References

- parent — _bmad-output/initiative-orchestration-control-plane/initiative-orchestration-control-plane.md, Requirements R7, R20, R21
- architecture — _bmad-output/initiative-orchestration-control-plane/architecture-orchestration-control-plane/architecture-orchestration-control-plane.md

## Notes

- Waits on epic-triggers because: the trigger engine and send to workers.
