---
id: 14
type: story
title: "Safe migrations across project files"
parent: epic-server-and-ledger
covers: [R1, R8]
after: [4, 10]
hitl: false
risk: medium
tracker_id: "23"
remote: "https://github.com/mgd43b/plotroom/issues/23"
tracker_status: backlog
status: built
---

# Safe migrations across project files

## Description

An online backup of each project file before it migrates, refusal to open a file whose schema is newer than the code, one bad file leaving only its project unavailable (the readiness state entry 10's /readyz reports), and a migration test running every revision against several project files.

## Requirements

Verify: In CI, migrating three fixture files where one is corrupt leaves the other two migrated and backed up and reports the third not ready; a file stamped with a newer revision is refused.

## References

- parent — _bmad-output/initiative-orchestration-control-plane/tickets/epic-server-and-ledger/epic-server-and-ledger.md

## Notes

- Hands on: the online backup to epic 11's scheduled backups (entry 11.4).

- Built: https://github.com/mgd43b/plotroom/pull/183
