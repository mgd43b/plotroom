---
id: 12
type: story
title: "Refactor sweep"
parent: epic-server-and-ledger
after: [2, 3, 4, 5, 6, 7, 8, 13, 9, 10, 14, 15, 11, 17, 18, 19, 20, 21, 22, 23]
hitl: false
risk: low
tracker_id: "26"
remote: "https://github.com/mgd43b/plotroom/issues/26"
tracker_status: backlog
---

# Refactor sweep

## Description

Cleanup only, scoped at its start from the plans and review findings the epic deferred, including the stdio proxy blocking on readline when a client breaks stdout but keeps stdin open and silent (deferred in PR #51), and the unbounded writer queue in src/plotroom/core/writer.py (declined in PR #50; a cap needs a backpressure policy that belongs with the transport limits), and logged_reason reading only the active log file, so with a tiny size limit a failure line can roll into .1 before the parent reads it (low; cannot happen at the real limits; found in PR #132). It also covers the port-file test after a stop: it checks the file only once the daemon has exited, so it does not prove the daemon unpublishes before HTTP shutdown finishes. The sweep pauses HTTP shutdown in an existing in-process daemon test and asserts the file is already gone. Production already unpublishes first (found in Kodus review of PR #135).

## Requirements

Verify: Deferred findings are resolved or re-filed, and CI passes.

## References

- parent — _bmad-output/initiative-orchestration-control-plane/tickets/epic-server-and-ledger/epic-server-and-ledger.md
