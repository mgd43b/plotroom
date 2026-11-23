---
id: 2
type: story
title: "Index Claude reports, comments, PR bodies, decisions, asks and flakes"
parent: epic-search
covers: [R1]
after: [1, 2.9, 2.5, 4.4, 7.1, 7.2, 4.6]
hitl: false
risk: low
tracker_id: "116"
remote: "https://github.com/mgd43b/plotroom/issues/116"
tracker_status: backlog
---

# Index Claude reports, comments, PR bodies, decisions, asks and flakes

## Description

Claude turn reports and messages, issue comments and PR bodies from the projection, decisions (their exact words), asks and flake candidates are indexed as they are written, and search() filters by kind.

## Requirements

Verify: A turn report, an issue comment, a PR body, a decision, an ask and a flake are each found by a word from them, and kinds=[decision] returns only decisions.

## References

- parent — _bmad-output/initiative-orchestration-control-plane/tickets/epic-search/epic-search.md
