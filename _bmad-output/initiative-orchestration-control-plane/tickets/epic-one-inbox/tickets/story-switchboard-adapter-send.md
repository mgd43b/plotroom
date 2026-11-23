---
id: 17
type: story
title: "Switchboard adapter: send"
parent: epic-one-inbox
covers: [R3]
after: [7, 1.7]
hitl: false
risk: medium
tracker_id: "41"
remote: "https://github.com/mgd43b/plotroom/issues/41"
tracker_status: done
---

# Switchboard adapter: send

## Description

Entry 7's participant gains a send that actions use, each message carrying its action_key, delivered at least once. Hands on: the send to epic 3's trigger actions and to entry 8.

## Requirements

Verify: A test action's send reaches the scripted participant with its action_key, and a crash after sending and before the action is marked done sends it again with the same key.

## References

- parent — _bmad-output/initiative-orchestration-control-plane/tickets/epic-one-inbox/epic-one-inbox.md
- Built: https://github.com/mgd43b/plotroom/pull/145
