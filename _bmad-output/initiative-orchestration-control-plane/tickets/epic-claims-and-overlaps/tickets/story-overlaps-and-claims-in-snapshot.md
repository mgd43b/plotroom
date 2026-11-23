---
id: 4
type: story
title: "overlaps() and claims in snapshot()"
parent: epic-claims-and-overlaps
covers: [R3]
after: [2, 3]
hitl: false
risk: low
tracker_id: "79"
remote: "https://github.com/mgd43b/plotroom/issues/79"
tracker_status: backlog
---

# overlaps() and claims in snapshot()

## Description

overlaps(pr | lane) returns current conflicts, tree-move items included, for a PR or a lane; claims(lane?) lists claims with reasons; snapshot() shows claim and overlap counts. Hands on: claims() to epic 6's don't-touch section.

## Requirements

Verify: overlaps for a lane and for a PR each return the same conflicts their items show, claims lists each claim with its reason, and snapshot() counts both.

## References

- parent — _bmad-output/initiative-orchestration-control-plane/tickets/epic-claims-and-overlaps/epic-claims-and-overlaps.md

## Notes

- Built: https://github.com/mgd43b/plotroom/pull/150
