---
id: 17
type: story
title: "Bounded server log"
parent: epic-server-and-ledger
covers: [R8]
after: [2]
hitl: false
risk: low
tracker_id: "90"
remote: "https://github.com/mgd43b/plotroom/issues/90"
tracker_status: backlog
---

# Bounded server log

## Description

plotroom-serve.log in the data directory rotates by size, keeping a configured number of files, so a long-running daemon's log never grows without bound (found by lane #14 in PR #51).

## Requirements

Verify: With a small size limit configured, writing past it rotates the log, keeps only the configured number of files, and the daemon keeps logging to the new file.

## References

- parent — _bmad-output/initiative-orchestration-control-plane/tickets/epic-server-and-ledger/epic-server-and-ledger.md
