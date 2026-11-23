---
id: 4
type: story
title: "The budget_ratio trigger leaf"
parent: epic-guardrails
covers: [R2]
after: [1, 3.1]
hitl: false
risk: low
tracker_id: "104"
remote: "https://github.com/mgd43b/plotroom/issues/104"
tracker_status: backlog
---

# The budget_ratio trigger leaf

## Description

Adds budget_ratio(run) >= x to epic 3's leaf registry, a level condition over a budget's latest reading.

## Requirements

Verify: A trigger on budget_ratio(run) >= 0.9 fires once when a fixture reading crosses 90%, and not again on a later reading above it.

## References

- parent — _bmad-output/initiative-orchestration-control-plane/tickets/epic-guardrails/epic-guardrails.md
