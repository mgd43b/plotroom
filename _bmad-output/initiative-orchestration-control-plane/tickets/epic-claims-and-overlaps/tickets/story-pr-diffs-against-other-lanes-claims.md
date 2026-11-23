---
id: 2
type: story
title: "PR diffs against other lanes' claims"
parent: epic-claims-and-overlaps
covers: [R1]
after: [1, 2.15]
hitl: false
risk: medium
tracker_id: "77"
remote: "https://github.com/mgd43b/plotroom/issues/77"
tracker_status: backlog
---

# PR diffs against other lanes' claims

## Description

Every PR's changed files in the projection are checked against other lanes' claims (a PR with no lane against every claim) as a state-backed item opened before review, naming the PR, the files and the claiming lane, and closed when the overlap clears.

## Requirements

Verify: A PR from lane A touching a file lane B claims opens one item and none for lane A's own claims; a claim made after the PR's head opens it too; pushing a head without the file or releasing the claim closes it; a PR with no lane is checked against every claim.

## References

- parent — _bmad-output/initiative-orchestration-control-plane/tickets/epic-claims-and-overlaps/epic-claims-and-overlaps.md

## Notes

- Built: https://github.com/mgd43b/plotroom/pull/150
