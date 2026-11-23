---
id: 6
type: story
title: "Storing goals, goal() and the first goal"
parent: epic-human-queue
covers: [R3]
after: [5, 1, 2.16]
hitl: false
risk: medium
tracker_id: "87"
remote: "https://github.com/mgd43b/plotroom/issues/87"
tracker_status: backlog
---

# Storing goals, goal() and the first goal

## Description

goal_set(name, text), orchestrator only and declared in the writer-rule table, stores a goal after it parses; goal(name?) reports whether a stored goal holds and, for each remaining item, why it is still open; the first goal ships as an example.

## Requirements

Verify: goal_set stores the first goal and a worker token's goal_set is refused; on fixture issues goal() reports done only when every open issue is the human's or blocked by one, and names each remaining issue with its failing clause.

## References

- parent — _bmad-output/initiative-orchestration-control-plane/tickets/epic-human-queue/epic-human-queue.md
