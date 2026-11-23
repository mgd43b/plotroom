---
id: 3
type: story
title: "Ask checks: buried, optionless, stale and orphaned"
parent: epic-human-queue
covers: [R2]
after: [1, 2, 1.7]
hitl: false
risk: medium
tracker_id: "85"
remote: "https://github.com/mgd43b/plotroom/issues/85"
tracker_status: done
---

# Ask checks: buried, optionless, stale and orphaned

## Description

On each relevant event and tick, using comment authors and times from the projection and the configured human_login, an ask is flagged with an item when a newer comment by someone else follows it on its issue, when it has no options, when it is older than a configured number of days, or when its issue closed with no reply from the human after the ask.

## Requirements

Verify: Fixture issues raise exactly one item for each of the four cases, a newer comment by the human does not count as burying, and each item closes when its condition clears.

## References

- parent — _bmad-output/initiative-orchestration-control-plane/tickets/epic-human-queue/epic-human-queue.md
