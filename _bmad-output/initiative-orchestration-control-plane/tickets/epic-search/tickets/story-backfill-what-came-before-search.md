---
id: 3
type: story
title: "Backfill what came before search"
parent: epic-search
covers: [R2]
after: [2, 4]
hitl: false
risk: low
tracker_id: "117"
remote: "https://github.com/mgd43b/plotroom/issues/117"
tracker_status: done
---

# Backfill what came before search

## Description

Records written before search landed are indexed by a backfill on entry 4's follower, through the writer.

## Requirements

Verify: On a project file with records from before the migration, the backfill indexes all of them, stopping it halfway and restarting it indexes the rest once, and search finds the oldest.

## References

- parent — _bmad-output/initiative-orchestration-control-plane/tickets/epic-search/epic-search.md
