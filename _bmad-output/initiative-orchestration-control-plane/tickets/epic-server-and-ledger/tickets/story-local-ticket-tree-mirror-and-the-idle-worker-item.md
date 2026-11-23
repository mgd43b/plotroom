---
id: 13
type: story
title: "Local ticket-tree mirror and the idle-worker item"
parent: epic-server-and-ledger
covers: [R3, R9]
after: [7, 8]
hitl: false
risk: medium
tracker_id: "20"
remote: "https://github.com/mgd43b/plotroom/issues/20"
tracker_status: done
---

# Local ticket-tree mirror and the idle-worker item

## Description

A WorkItemSource adapter reads a BMad repo ticket tree at a local path (tickets.toml entries and plan status) into a read-only mirror, refreshed on ticks, with readiness as the BMad ticket tooling's next step defines it (a ready-to-start entry); a tree whose store is a tracker is out of scope here and comes with epic 2; a worker with no lane while ready work exists raises 'idle worker with unblocked work'. Hands on: the WorkItemSource port to epic 2 (default-branch reader) and epic 5.

## Requirements

Verify: Against a fixture tree, the mirror lists its ready entries, never writes the tree, and a configured worker with no lane raises one idle-worker item that closes when a lane starts.

## References

- parent — _bmad-output/initiative-orchestration-control-plane/tickets/epic-server-and-ledger/epic-server-and-ledger.md
- .claude/skills/bmad-agent-po/scripts/tickets.py

## Notes

- Decision: 2026-11-22, the owner chose the BMad repo tree for the local mirror; GitHub Issues as a work-item source comes with epic 2's GitHub adapter.
- Hands on: the work-item mirror to epic 6's brief() (entry 6.1).
- Built: https://github.com/mgd43b/plotroom/pull/144
