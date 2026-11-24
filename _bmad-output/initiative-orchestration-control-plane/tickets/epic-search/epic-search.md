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
after: []
refined: true
risk: medium
status: done
---

# Search

## Description

The orchestrator finds past events, decisions, asks, lane reports and flakes by full text everywhere and by meaning where an embedder is deployed, within one project, including records written before search landed.

## Outcome

The orchestrator can look up what was already decided or seen, instead of asking again.

## Requirements

- R1 (parent R28): search() finds events, decisions, asks, lane reports and flakes by meaning where an embedder is configured and by full text otherwise, returning ids and excerpts.
- R2 (parent R28): Records written before search landed are indexed by a resumable backfill.
- R3 (parent R27, C8): Search never crosses projects, and results stay within the token budget.
- R4 (parent C15; spine AD-6): The embedder is a pluggable HTTP endpoint or none, computed off the writer, and changing the model reindexes.

## Constraints

- From the initiative: C8 (token budgets), C15 (one search interface, optional embedder).
- From the spine: AD-5, AD-6 and AD-13 bind this epic's stories.

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

- Waits on epic-server-and-ledger, story by story, for: the event log and unit of work.
- Waits on epic-one-inbox, story by story, for: inbox events and lane reports.
- Waits on epic-merge-controller, story by story, for: flake records.
- Waits on epic-human-queue, story by story, for: decisions and asks.
- Decision: 2026-11-22, build order 1 (full text over messages, the tracer), 2, 3, 4 (embeddings, the least certain), 5, with a refactor sweep (6) and an end-to-end suite (7); tests use a fake embedder and the real one is chosen per deployment. Prerequisites are story by story, with no whole-epic gate.
- Decision: 2026-11-22, from the set check: the tracer indexes switchboard messages only; embeddings (entry 4, the least certain) come second and own the seq follower the backfill reuses; issue comments and PR bodies are indexed too; the fake embedder is served over HTTP.
