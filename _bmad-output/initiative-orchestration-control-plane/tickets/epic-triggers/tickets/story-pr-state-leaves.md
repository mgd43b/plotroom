---
id: 2
type: story
title: "PR-state leaves"
parent: epic-triggers
covers: [R1]
after: [11, 2.3, 2.4, 2.5]
hitl: false
risk: low
tracker_id: "56"
remote: "https://github.com/mgd43b/plotroom/issues/56"
tracker_status: backlog
---

# PR-state leaves

## Description

Adds the leaves pr_green(n), pr_threads_resolved(n) and issue_closed(n) to the registry, level conditions over the projection.

## Requirements

Verify: Each leaf holds exactly when the projection shows the state: a test PR's checks going green, its last thread resolving, and a test issue closing each fire one trigger.

## References

- parent — _bmad-output/initiative-orchestration-control-plane/tickets/epic-triggers/epic-triggers.md
