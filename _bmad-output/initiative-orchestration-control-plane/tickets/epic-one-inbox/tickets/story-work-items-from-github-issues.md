---
id: 16
type: story
title: "Work items from GitHub Issues"
parent: epic-one-inbox
covers: [R8]
after: [5, 10, 1.13]
hitl: false
risk: medium
tracker_id: "38"
remote: "https://github.com/mgd43b/plotroom/issues/38"
tracker_status: done
---

# Work items from GitHub Issues

## Description

Fetchers add milestones, sub-issues, blocked-by relations and issue labels to the projection, and a WorkItemSource adapter reads a BMad github-store tree from them into the read-only work-item mirror when the project's config names GitHub Issues as its one work-item source (each project has exactly one: a local tree, a tree on the default branch, or GitHub Issues), with readiness from the github store's mapping (the starter config plus the project's store config) as tickets.py next defines ready_to_start, so plotroom can see a project's own GitHub Issues tickets. Uses entry 2's credential provider.

## Requirements

Verify: Against fixture issues on the test repository shaped like this project's tickets (an epic with sub-issues, a blocker in review), the mirror lists the same ready entries tickets.py next reports for them and never writes an issue.

## References

- parent — _bmad-output/initiative-orchestration-control-plane/tickets/epic-one-inbox/epic-one-inbox.md
- .claude/skills/bmad-agent-po/config/github-ticketing.toml
- _bmad/custom/ticketing-store-config.toml
- Built: https://github.com/mgd43b/plotroom/pull/147

## Notes

- Decision: 2026-11-22, the owner added this story ('Add a story') so plotroom can read this project's own GitHub Issues tickets.
