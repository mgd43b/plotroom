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
status: built
---

# Bounded server log

## Description

plotroom-serve.log in the data directory rotates by size, keeping a configured number of files, so a long-running daemon's log never grows without bound (found by lane #14 in PR #51).

## Requirements

Verify: With a small size limit configured, writing past it rotates the log, keeps only the configured number of files, and the daemon keeps logging to the new file.

## References

- parent — _bmad-output/initiative-orchestration-control-plane/tickets/epic-server-and-ledger/epic-server-and-ledger.md

## Notes

- Decision: the on-demand daemon rotates its own log (`serve --log-file`); `log_max_bytes` (10 MiB) and `log_backups` (5) are config keys with `PLOTROOM_LOG_MAX_BYTES` and `PLOTROOM_LOG_BACKUPS` overrides.
- Built: https://github.com/mgd43b/plotroom/pull/132
