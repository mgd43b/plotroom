---
id: 7
type: story
title: "Actions, action runner and ticks"
parent: epic-server-and-ledger
covers: [R1, R3, R6]
after: [4, 6]
hitl: false
risk: medium
tracker_id: "18"
remote: "https://github.com/mgd43b/plotroom/issues/18"
tracker_status: backlog
---

# Actions, action runner and ticks

## Description

Adds the actions table with cause-derived action_keys, the action runner executing committed actions after commit with a retry limit (its system writes declared in the writer table), a failed action opening a next item, and core.tick events from Clock at the configured cadence. Hands on: the action runner to epic 3.

## Requirements

Verify: Under a fake Clock, an action that fails past the retry limit opens one failed-action item, also across a restart, and ack closes it; ticks arrive at the configured cadence.

## References

- parent — _bmad-output/initiative-orchestration-control-plane/tickets/epic-server-and-ledger/epic-server-and-ledger.md

## Notes

- Hands on from #15 (PRs #50, #92): event kinds and commands register in core/catalog.py build_catalog(); an event's source must equal its kind's prefix; a duplicate returns the stored event whatever its fields, only after role, kind and source pass; migration 0002 carries CHECK(seq > 0) and CHECK(json_valid(fields)), and the next revision's parent is 0002; Ledger.execute checks no role yet.
