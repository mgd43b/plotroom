---
id: 20
type: bug
title: "lock_owner's probe can briefly take a stale lock"
parent: epic-server-and-ledger
covers: [R8]
after: [2]
refined: false
hitl: false
risk: low
tracker_id: "130"
remote: "https://github.com/mgd43b/plotroom/issues/130"
tracker_status: done
status: built
---

# lock_owner's probe can briefly take a stale lock

## Description

When a stale lock file names a live pid, lock_owner's probe can briefly take the lock; it heals itself but logs one spurious "locked" line (found by the #91 lane). The probe should read the lock's owner without taking it.

## Requirements

Verify: With a stale lock file naming a live pid, lock_owner reports the owner without taking the lock, and no "locked" line is logged.

## References

- parent — _bmad-output/initiative-orchestration-control-plane/tickets/epic-server-and-ledger/epic-server-and-ledger.md

## Notes

- Built: https://github.com/mgd43b/plotroom/pull/137
- Verified: 2026-11-22. The lane passed 21 focused lock and daemon-start tests that run independently of signal replay. Both Python jobs passed on merge 9a4db649 in https://github.com/mgd43b/plotroom/actions/runs/45117133037.
