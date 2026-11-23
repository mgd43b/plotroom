---
id: 1
type: story
title: "Tracer: a pr_merged trigger notifies the orchestrator once"
parent: epic-triggers
covers: [R1, R2, R7]
after: [1.6, 1.7, 2.2]
hitl: false
risk: high
tracker_id: "52"
remote: "https://github.com/mgd43b/plotroom/issues/52"
tracker_status: done
---

# Tracer: a pr_merged trigger notifies the orchestrator once

## Description

The thinnest path: a leaf registry (name, argument schema of exact identities, evaluator, level or event) that later entries and epics append to; an orchestrator-only trigger command (with an optional lane, defaulting to the action's target) storing a predicate of one pr_merged(n) leaf and a notify_orchestrator(text) action; after every unit of work the engine evaluates open triggers, and a once trigger fires in the first unit of work its predicate holds (at creation too), its fired state and an actions row written in the same transaction; the action opens a next item. Hands on: the leaf registry to every later entry and to epics 6 (checklist_empty) and 8 (budget_ratio); trigger records and fired state to epic 9's import.

## Requirements

Verify: A trigger on pr_merged for a test PR opens exactly one next item when the PR merges, and one created after the merge fires at creation; a worker token's trigger call is refused.

## References

- parent — _bmad-output/initiative-orchestration-control-plane/tickets/epic-triggers/epic-triggers.md
- architecture-orchestration-control-plane/architecture-orchestration-control-plane.md#ad-15

## Notes

- Check beyond its criteria: a trigger evaluated during a burst of events fires once, not once per event.
- Built: https://github.com/mgd43b/plotroom/pull/148
