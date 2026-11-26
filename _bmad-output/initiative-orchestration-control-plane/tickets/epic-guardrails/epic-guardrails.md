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
after: []
refined: true
risk: low
---

# Guardrails

## Description

A usage adapter reads provider spend; paid runs carry hard caps that raise next() items at 50% and 80% and stop the lane at 100%; and hands-on requests are routed to their owning lane.

## Outcome

Paid runs cannot overrun their caps unnoticed, and hands-on work is routed away from the orchestrator.

## Requirements

- R1 (parent R7, R20; spine AD-17): Provider spend arrives through a UsageMeter adapter as cumulative spend per run with an as-of time, read with a credential the tool never stores.
- R2 (parent R20): A paid run registers a hard cap, meter source and key scope; items open at 50% and 80%, stop is sent to the lane at 100%, each once, and budget_ratio is a trigger leaf.
- R3 (parent R21): A hands-on request returns the lane routed for its kind, and an unrouted one opens an item for the orchestrator.

## Constraints

- From the initiative: C2 (credentials stay outside records), C3 (no trigger action spends), C5 (keyed, once-only thresholds).
- From the spine: AD-12, AD-14, AD-17 and AD-18 bind this epic's stories.

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

- Waits on epic-server-and-ledger, story by story, for: next items, the action runner and the container.
- Waits on epic-triggers, story by story, for: the trigger engine and send to workers.
- Decision: 2026-11-22, build order 1 (the tracer, on a fixture meter), 2, 3 (the first real provider, the least certain, with a plan checkpoint), 4, 5, with a refactor sweep (6) and an end-to-end suite (7); the first provider and its reporting endpoint are confirmed against its current documentation at entry 3's plan checkpoint. Prerequisites are story by story, with no whole-epic gate.
- Decision: 2026-11-22, from the set check: budgets name their run's lane and the first provider (entry 3, the least certain) is built second. The Anthropic-first selection was superseded on 2026-11-26 by OpenRouter. See #102 for the owner decision and current endpoint.
