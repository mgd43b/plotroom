---
id: 8
type: story
title: "Controller-run merges, built off"
parent: epic-merge-controller
covers: [R5]
after: [5, 3, 7]
hitl: false
risk: medium
tracker_id: "73"
remote: "https://github.com/mgd43b/plotroom/issues/73"
tracker_status: done
---

# Controller-run merges, built off

## Description

A per-project setting, off by default, lets the controller itself merge a MERGE verdict with the configured method, pinned to the head SHA, through entry 3's write adapter; with it off the adapter refuses to merge.

## Requirements

Verify: With the setting off no merge call is possible; with it on in a test project the controller merges a green test PR pinned to its head and logs it as gated.

## References

- parent — _bmad-output/initiative-orchestration-control-plane/tickets/epic-merge-controller/epic-merge-controller.md
