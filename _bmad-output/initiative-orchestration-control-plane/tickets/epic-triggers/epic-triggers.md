---
tracker_id: "3"
remote: https://github.com/mgd43b/plotroom/issues/3
type: epic
title: "Triggers"
parent: initiative-orchestration-control-plane
covers: [R5, R6]
sources: []
companions:
  - _bmad-output/initiative-orchestration-control-plane/architecture-orchestration-control-plane/architecture-orchestration-control-plane.md
after: []
refined: true
risk: medium
status: done
---

# Triggers

## Description

The orchestrator registers 'when X, then Y' triggers bound to exact identities; they fire exactly once per transition on inbox events, lane run reports and ticks; their sends reach Codex workers through the switchboard or bridge and Claude lanes through the drained outbox.

## Outcome

"When X, then Y" promises live as records and fire on their own, so none is forgotten or sent twice.

## Requirements

- R1 (parent R5): Trigger predicates are built from the leaves pr_merged, pr_green, pr_threads_resolved, issue_closed, run_finished, main_green_with_images, message_from, deploy_finished and lane_idle, composed with all_of and any_of, over the GitHub projection, the ledger and inbox events.
- R2 (parent R5; spine AD-15): A once trigger fires in the first unit of work in which its predicate holds, including at creation, with its fired state and actions written in the same transaction, and never fires twice across restarts or replays.
- R3 (parent R5, R6; spine AD-15): An every trigger fires on each false-to-true transition; a branch-glob leaf binds to the first matching PR and records it; a predicate with free text or an unknown leaf is refused.
- R4 (parent R5; decision 2026-11-22): A lane reports its own runs with run_report, and main_green_with_images holds when main's latest completed run is on the current main SHA and both the run and all its configured image-build checks have passed.
- R5 (parent R5): triggers(lane?) lists triggers with their bindings and states, cancel(trigger_id) stops one, and snapshot() counts them.
- R6 (parent R5; spine AD-10): Actions are only send, notify_orchestrator, mark and propose_archive; a send reaches a Codex worker through the switchboard and a Claude lane through the outbox the configured drain session leases and completes, at least once, carrying its action key.
- R7 (parent R5, R22): Only an orchestrator token creates or cancels a trigger.

## Constraints

- From the initiative: C3 (no action merges, deploys, spends or posts to a human), C5 (keyed actions, at-least-once delivery), C7 (triggers never come from event text).
- From the spine: AD-10, AD-14, AD-15 and AD-18 bind this epic's stories.

## Done when

- A trigger on `all_of(pr_merged(a), pr_merged(b))` sends its message exactly once when the second PR merges, including across a server restart between the merges.
- A send to a Codex worker arrives through the switchboard and a send to a Claude lane arrives through the drain session, each carrying its action key.
- A trigger with free text in its predicate is refused, and only an orchestrator token can create one.
- A lane's run report fires `run_finished`, and `main_green_with_images` holds only once main's image checks pass.

## Boundaries

- Out: budget thresholds (epic 8) and archiving rules (epic 6).
- Owns the trigger engine and leaf registry, `all_of` and `any_of`, the M2 actions (send picks the channel), predicate leaves over inbox events, `run_report` and `main_green_with_images`, the Claude-lane outbox, `outbox_lease`, `outbox_done` and the drain stand-in; switchboard delivery itself is epic 2's (2.17, 2.8).
- Out: the `checklist_empty` leaf (epic 6) and the `budget_ratio` leaf (epic 8).

## References

- parent — _bmad-output/initiative-orchestration-control-plane/initiative-orchestration-control-plane.md, Requirements R5, R6
- architecture — _bmad-output/initiative-orchestration-control-plane/architecture-orchestration-control-plane/architecture-orchestration-control-plane.md

## Notes

- Waits on epic-server-and-ledger, story by story, for: the drain role and config, the actions table and the action runner.
- Waits on epic-one-inbox, story by story, for: github and switchboard events and the GitHub projection.
- Decision: 2026-11-22, the tracer bullet is entry 1 (a pr_merged trigger notifying the orchestrator); entry 5 (exactly once across restarts), the least certain, follows it. Prerequisites are story by story on epics 1 and 2, with no whole-epic gate.
- Decision: 2026-11-22, the engine chain runs 1, 5, 8, 11, 2, 3, 4; the action chain 6 and 7 runs alongside it once epic 2's send exists; a refactor sweep (9) and an end-to-end suite (10) close the epic. Triggers carry an optional lane at creation, defaulting to the action's target; every PR leaf accepts a branch glob; a person runs one real drain session (entry 7).
