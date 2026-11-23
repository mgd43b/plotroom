---
id: 3
type: story
title: "Ticket-tree moves against open PRs"
parent: epic-claims-and-overlaps
covers: [R2]
after: [2, 2.10]
hitl: false
risk: low
tracker_id: "78"
remote: "https://github.com/mgd43b/plotroom/issues/78"
tracker_status: backlog
---

# Ticket-tree moves against open PRs

## Description

Using the renames and previous paths entry 2.15 records, a PR that moves files under the project's ticket-tree path (from the work-item source config) while other open PRs edit those files opens an item listing the editing PRs and a suggested order (those PRs merge first). Uses the fixture tree entry 2.10 puts on the test repository.

## Requirements

Verify: A PR moving a story file that two open PRs edit opens one item naming both and suggesting they merge first; a move of an untouched file opens none.

## References

- parent — _bmad-output/initiative-orchestration-control-plane/tickets/epic-claims-and-overlaps/epic-claims-and-overlaps.md
