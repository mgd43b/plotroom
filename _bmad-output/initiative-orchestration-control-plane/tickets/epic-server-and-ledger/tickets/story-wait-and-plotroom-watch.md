---
id: 9
type: story
title: "wait() and plotroom watch"
parent: epic-server-and-ledger
covers: [R3, R5]
after: [5, 7]
hitl: false
risk: medium
tracker_id: "21"
remote: "https://github.com/mgd43b/plotroom/issues/21"
tracker_status: backlog
---

# wait() and plotroom watch

## Description

wait(cursor, timeout) returns on the first row visible to the caller's role with seq above the cursor, caps at a configurable limit (240 seconds by default) with progress notifications, and treats no cursor as now; plotroom watch loops it until a row arrives; the cursor is a field added to the results of snapshot() and next(); each role in the project config's watchers list (by default orchestrator, clerk and the drain session) with no wait() held across ticks raises 'watcher not running'.

## Requirements

Verify: With the cap set to 2 seconds, a background plotroom watch keeps waiting across several caps, exits within a second of a new item, and stopping it raises a watcher-not-running item on the next tick.

## References

- parent — _bmad-output/initiative-orchestration-control-plane/tickets/epic-server-and-ledger/epic-server-and-ledger.md

## Notes

- Built: https://github.com/mgd43b/plotroom/pull/144
