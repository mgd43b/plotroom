---
id: 11
type: story
title: "Rule 2: unresolved review threads"
parent: epic-merge-controller
covers: [R2]
after: [1, 2.4]
hitl: false
risk: medium
tracker_id: "64"
remote: "https://github.com/mgd43b/plotroom/issues/64"
tracker_status: backlog
---

# Rule 2: unresolved review threads

## Description

Rule 2 as in safemerge.sh: any unresolved review thread refuses, and a thread list the projection cannot show complete (its pagination did not finish, or it is not yet fetched) refuses; past 100 threads, a fully paginated list is judged normally, unlike safemerge.sh.

## Requirements

Verify: A PR with one unresolved thread is refused and passes once it is resolved; a PR whose thread list is incomplete is refused with that reason, and a PR with more than 100 resolved threads, fully paginated, passes rule 2.

## References

- parent — _bmad-output/initiative-orchestration-control-plane/tickets/epic-merge-controller/epic-merge-controller.md
