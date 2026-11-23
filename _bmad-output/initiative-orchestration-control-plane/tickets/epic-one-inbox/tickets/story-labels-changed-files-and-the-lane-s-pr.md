---
id: 15
type: story
title: "Labels, changed files and the lane's PR"
parent: epic-one-inbox
covers: [R1, R11]
after: [4, 7, 1.8]
hitl: false
risk: medium
tracker_id: "35"
remote: "https://github.com/mgd43b/plotroom/issues/35"
tracker_status: backlog
---

# Labels, changed files and the lane's PR

## Description

A fetcher adds labels with who applied them and the files each PR changes, with each file's rename status and previous path; core derives a lane's PR by matching its branch, extends entry 7's lane-inference hook so every github.* event on that PR carries the lane, and checks lane_ready arguments against the projection (the handoff from epic 1 entry 8). Hands on: labels to epic 4 (fix, allow-overlap and keywords-ok labels), changed files and renames to epic 4 (stale CI, flakes, one PR per lane) and epic 5 (overlaps, tree moves).

## Requirements

Verify: Labeling a PR and pushing a file change each appear once with the label's actor and the file; a lane's PR is found from its branch, and github.* events from every fetcher on that PR (PR, push, check, thread, label) carry the lane; lane_ready with a head SHA the projection does not show is refused.

## References

- parent — _bmad-output/initiative-orchestration-control-plane/tickets/epic-one-inbox/epic-one-inbox.md
