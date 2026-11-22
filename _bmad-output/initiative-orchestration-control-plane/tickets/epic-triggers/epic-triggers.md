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
after: [epic-server-and-ledger, epic-one-inbox]
refined: false
risk: medium
---

# Triggers

## Description

The orchestrator registers 'when X, then Y' triggers bound to exact identities; they fire exactly once per transition on inbox events, lane run reports and ticks; their sends reach Codex workers through the switchboard or bridge and Claude lanes through the drained outbox.

## Outcome

"When X, then Y" promises live as records and fire on their own, so none is forgotten or sent twice.

## Done when

- A trigger on `all_of(pr_merged(a), pr_merged(b))` sends its message exactly once when the second PR merges, including across a server restart between the merges.
- A send to a Codex worker arrives through the switchboard and a send to a Claude lane arrives through the drain session, each carrying its action key.
- A trigger with free text in its predicate is refused, and only an orchestrator token can create one.
- A lane's run report fires `run_finished`, and `main_green_with_images` holds only once main's image checks pass.

## Boundaries

- Out: budget thresholds (epic 8) and archiving rules (epic 6).
- Owns the trigger engine with `all_of` and `any_of`, the M2 actions, predicate leaves over inbox events, `run_report` (a lane reports its run finished) and `main_green_with_images` over configured image checks, worker delivery, the Claude-lane outbox, `outbox_done` and the drain lease.
- Out: the `checklist_empty` leaf (epic 6) and the `budget_ratio` leaf (epic 8).

## References

- parent — _bmad-output/initiative-orchestration-control-plane/initiative-orchestration-control-plane.md, Requirements R5, R6
- architecture — _bmad-output/initiative-orchestration-control-plane/architecture-orchestration-control-plane/architecture-orchestration-control-plane.md

## Notes

- Waits on epic-server-and-ledger because: the drain role and config, the actions table and the action runner.
- Waits on epic-one-inbox because: github and switchboard events and the GitHub projection.
