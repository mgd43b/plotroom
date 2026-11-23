---
id: 10
type: story
title: "Ticket tree from the default branch"
parent: epic-one-inbox
covers: [R8]
after: [2, 1.13]
hitl: false
risk: low
tracker_id: "37"
remote: "https://github.com/mgd43b/plotroom/issues/37"
tracker_status: backlog
---

# Ticket tree from the default branch

## Description

A WorkItemSource adapter reads a BMad repo-store ticket tree from the repository's default branch through entry 2's credential provider into the read-only mirror when the project's config names the default-branch tree as its one work-item source, refreshed on ticks. Hands on: the mirror to epic 5's tree-move checks.

## Requirements

Verify: Against a fixture tree on the test repository, the mirror lists the same ready entries as epic 1's local reader reads from a checkout of the same commit, and a push to the default branch updates it on the next tick.

## References

- parent — _bmad-output/initiative-orchestration-control-plane/tickets/epic-one-inbox/epic-one-inbox.md
- .claude/skills/bmad-agent-po/scripts/tickets.py
- architecture-orchestration-control-plane/architecture-orchestration-control-plane.md#ad-17
- Built: https://github.com/mgd43b/plotroom/pull/147
