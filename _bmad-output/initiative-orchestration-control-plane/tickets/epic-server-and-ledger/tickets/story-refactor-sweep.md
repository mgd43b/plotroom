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

Cleanup only, scoped at its start from the plans and review findings the epic deferred, including the stdio proxy blocking on readline when a client breaks stdout but keeps stdin open and silent (deferred in PR #51), and the unbounded writer queue in src/plotroom/core/writer.py (declined in PR #50; a cap needs a backpressure policy that belongs with the transport limits), and logged_reason reading only the active log file, so with a tiny size limit a failure line can roll into .1 before the parent reads it (low; cannot happen at the real limits; found in PR #132). Add focused regression coverage for these contracts from the reviews of PRs #134 and #135.

## Requirements

Verify: The port file is unpublished before paused HTTP shutdown finishes. Authentication refuses a digest mismatch for an existing token record. Concurrent local-token updates for separate instances preserve both records. A receipt replay is refused after its writer rule is withdrawn. The HTTP snapshot tool refuses a drain role. Deferred findings are resolved or re-filed, and CI passes.

## References

- parent — _bmad-output/initiative-orchestration-control-plane/tickets/epic-server-and-ledger/epic-server-and-ledger.md
- Review evidence — https://github.com/mgd43b/plotroom/pull/134 and https://github.com/mgd43b/plotroom/pull/135
