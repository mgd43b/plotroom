---
tracker_id: "10"
remote: https://github.com/mgd43b/plotroom/issues/10
type: epic
title: "Search"
parent: initiative-orchestration-control-plane
covers: [R28]
sources: []
companions:
  - _bmad-output/initiative-orchestration-control-plane/architecture-orchestration-control-plane/architecture-orchestration-control-plane.md
after: [epic-one-inbox, epic-merge-controller, epic-human-queue]
refined: false
risk: medium
---

# Search

## Description

The orchestrator finds past events, decisions, asks, lane reports and flakes by full text everywhere and by meaning where an embedder is deployed, within one project, including records written before search landed.

## Outcome

The orchestrator can look up what was already decided or seen, instead of asking again.

## Done when

- A full-text query returns excerpt hits linked to records in one project and never another.
- With an embedder configured, a query by meaning finds a paraphrased decision, and changing the model reindexes.
- Records written before search landed are backfilled.

## Boundaries

- Out: external vector stores (later adapters).

## References

- parent — _bmad-output/initiative-orchestration-control-plane/initiative-orchestration-control-plane.md, Requirements R28
- architecture — _bmad-output/initiative-orchestration-control-plane/architecture-orchestration-control-plane/architecture-orchestration-control-plane.md

## Notes

- Waits on epic-one-inbox because: inbox events and lane reports.
- Waits on epic-merge-controller because: flake records.
- Waits on epic-human-queue because: decisions and asks.
