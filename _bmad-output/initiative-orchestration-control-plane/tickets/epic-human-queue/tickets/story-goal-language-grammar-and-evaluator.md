---
id: 5
type: story
title: "Goal language: grammar and evaluator"
parent: epic-human-queue
covers: [R3]
after: [1, 1.8, 2.5]
hitl: false
risk: high
tracker_id: "83"
remote: "https://github.com/mgd43b/plotroom/issues/83"
tracker_status: done
---

# Goal language: grammar and evaluator

## Description

A small query language for goals over the projection and ledger (issues, labels, assignees, blocked-by, lanes, decisions), relational enough to say 'blocked by an issue that is', parsed and evaluated in core with no I/O, returning matching items with the clause each fails. The grammar is reviewed with the owner at the plan checkpoint. Hands on: the evaluator to entry 6.

## Requirements

Verify: Table-driven tests parse and evaluate goals over fixture state, including the first goal (every open issue in the milestone is assigned to the human or blocked by one that is); an invalid goal is refused with the position of the error; evaluation never writes.

## References

- parent — _bmad-output/initiative-orchestration-control-plane/tickets/epic-human-queue/epic-human-queue.md

## Notes

- Decision: 2026-11-22, the owner chose a small query language for goals ('A small query language'); it is the least certain story, so it follows the tracer.
- Check beyond its criteria: evaluating a goal over a large fixture board stays within snapshot()'s token budget when rendered.
