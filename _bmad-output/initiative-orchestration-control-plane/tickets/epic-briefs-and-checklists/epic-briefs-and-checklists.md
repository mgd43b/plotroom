---
tracker_id: "6"
remote: https://github.com/mgd43b/plotroom/issues/6
type: epic
title: "Briefs and checklists"
parent: initiative-orchestration-control-plane
covers: [R15, R16]
sources: []
companions:
  - _bmad-output/initiative-orchestration-control-plane/architecture-orchestration-control-plane/architecture-orchestration-control-plane.md
after: []
refined: true
risk: low
status: done
---

# Briefs and checklists

## Description

brief() fills a lane's brief with scope, claims to avoid and standing rules, and a READY lane's post-merge checklist becomes its active state until empty, when a checklist_empty trigger proposes the archive.

## Outcome

Every lane starts from a complete brief and ends with its follow-through tracked, so finished lanes get archived.

## Requirements

- R1 (parent R15): brief(work_item, worker) returns a brief filled from the work item, the lane's paths, other lanes' current claims as don't-touch, the plan and RESULT shapes and the configured standing rules; the orchestrator edits and sends it.
- R2 (parent R16): A lane's READY declares its post-merge checklist as structured rows, which become its active state when its PR merges, and lane_archive is refused while any step is open.
- R3 (parent R16, R3): A lane whose last step closes raises an archive item, and checklist_empty(lane) is a trigger leaf.

## Constraints

- From the initiative: C1 (no decisions), C7 (checklists are structured rows, never parsed text).
- From the spine: AD-8, AD-15 and AD-18 bind this epic's stories.

## Done when

- `brief()` returns a brief with a don't-touch section built from current claims and the standing rules.
- A lane's READY checklist becomes its active state on merge, archiving is refused while it has steps, and an empty checklist proposes the archive.

## Boundaries

- Out: claims themselves (epic 5) and the trigger engine (epic 3).
- Owns checklist rows and the `checklist_empty` predicate leaf.

## References

- parent — _bmad-output/initiative-orchestration-control-plane/initiative-orchestration-control-plane.md, Requirements R15, R16
- architecture — _bmad-output/initiative-orchestration-control-plane/architecture-orchestration-control-plane/architecture-orchestration-control-plane.md

## Notes

- Waits on epic-server-and-ledger, story by story, for: workers and lanes, and the local work-item mirror.
- Waits on epic-one-inbox, story by story, for: the lane's PR in the GitHub projection.
- Waits on epic-claims-and-overlaps, story by story, for: current claims for the don't-touch section.
- Waits on epic-triggers, story by story, for: the trigger engine and the propose_archive action.
- Decision: 2026-11-22, build order 1 (the tracer), 2, 3, 4, with a refactor sweep (5) and an end-to-end suite (6); the archive item opens without a trigger (only the orchestrator creates triggers), and checklist_empty is there for its own triggers. Prerequisites are story by story, with no whole-epic gate.
- Decision: 2026-11-22, from the set check: a brief's paths are the lane's own claims and its scope is the work item's full text; there is one archive item kind (entry 3), which epic 3's propose_archive reuses; checklist_empty holds only for an active checklist whose last step closed.
