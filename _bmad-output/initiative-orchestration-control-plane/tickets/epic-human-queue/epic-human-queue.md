---
tracker_id: "7"
remote: https://github.com/mgd43b/plotroom/issues/7
type: epic
title: "The human's queue"
parent: initiative-orchestration-control-plane
covers: [R17, R18, R19]
sources: []
companions:
  - _bmad-output/initiative-orchestration-control-plane/architecture-orchestration-control-plane/architecture-orchestration-control-plane.md
after: []
refined: true
risk: low
status: done
---

# The human's queue

## Description

Decisions are recorded in the human's exact words, asks on GitHub are flagged when buried, stale or unanswered, and goal() reports what is left and why.

## Outcome

The human's decisions are kept in their own words, and their asks stop getting buried.

## Requirements

- R1 (parent R19; constraint C6): decide() stores who, the exact words byte for byte, where and when, and what the decision governs; words cited from a GitHub comment must appear in it; decisions link to the asks and tickets they settle.
- R2 (parent R18): Asks the orchestrator posted are recorded, and an ask is flagged when a newer comment buries it, it has no options, it is older than a configured number of days, or its issue closed without an answer.
- R3 (parent R17; decision 2026-11-22): Goals are written in a small query language over the projection and ledger, and goal() reports whether one holds and why each remaining item is open.

## Constraints

- From the initiative: C1 (no decisions), C3 (the tool never posts to a human), C6 (exact words).
- From the spine: AD-2, AD-8, AD-13 and AD-18 bind this epic's stories.

## Done when

- `decide()` stores words byte for byte and rejects words not found in the cited GitHub comment.
- A buried, optionless, stale or orphaned ask is flagged in `next()`.
- `goal()` reports what is left of an encoded goal and why.
- A goal written in the query language is stored, evaluated without writing anything, and reports each remaining item with the clause it fails.

## Boundaries

- Out: making decisions (C1).

## References

- parent — _bmad-output/initiative-orchestration-control-plane/initiative-orchestration-control-plane.md, Requirements R17, R18, R19
- architecture — _bmad-output/initiative-orchestration-control-plane/architecture-orchestration-control-plane/architecture-orchestration-control-plane.md

## Notes

- Waits on epic-server-and-ledger, story by story, for: the writer-rule table and ticks.
- Waits on epic-one-inbox, story by story, for: issues and comments in the GitHub projection.
- Decision: 2026-11-22, the owner chose a small query language for goals ('A small query language'). The tracer bullet is entry 1 (decide); the goal language (entry 5), the least certain, has its own story. Prerequisites are story by story on epics 1 and 2, with no whole-epic gate.
- Decision: 2026-11-22, build order 1, 5 (the goal language, least certain, with a plan checkpoint), 2, 3, 4, 6, with a refactor sweep (7) and an end-to-end suite (8); the human's GitHub login is project config (human_login); a comment not yet polled returns a retryable unavailable error from decide() and ask().
