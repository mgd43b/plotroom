---
id: 4
type: story
title: "Retire the hand-run clerk"
parent: epic-clerk-migration
covers: [R2]
after: [3, 4.10]
hitl: false
risk: low
tracker_id: "111"
remote: "https://github.com/mgd43b/plotroom/issues/111"
tracker_status: backlog
---

# Retire the hand-run clerk

## Description

After the working day, coordination/README.md, AGENTS.md, coordination/goals/ (clerk, coordinator and task-coordinator) and the copied coordinator and task-coordinator skills (edited copies, as coordination/README.md allows when a rule really differs) point at plotroom instead of the clerk's scripts; the copied clerk skill is removed from .claude/skills and .agents/skills, keeping a pinned copy of safemerge.sh and lib.sh under tests/fixtures for epic 4's comparison suite.

## Requirements

Verify: No file outside tests/fixtures tells a session to run sweep.sh, safemerge.sh or mergewait.sh, the coordinator's goal names plotroom's tools, and epic 4's comparison suite still passes against the pinned copy.

## References

- parent — _bmad-output/initiative-orchestration-control-plane/tickets/epic-clerk-migration/epic-clerk-migration.md
