---
id: 6
type: story
title: "Actions: send, mark and propose_archive"
parent: epic-triggers
covers: [R6]
after: [1, 1.8, 2.17]
hitl: false
risk: medium
tracker_id: "59"
remote: "https://github.com/mgd43b/plotroom/issues/59"
tracker_status: backlog
---

# Actions: send, mark and propose_archive

## Description

Completes the closed action set: send(worker, text), mark(lane, state) and propose_archive(lane) opening a next item; any other action is refused. send picks the channel from the worker's config (a Codex worker's switchboard address through epic 2's send, which a hosted instance turns into rows the bridge leases; a Claude lane through entry 7's outbox) and runs for any cause-derived action key, not only triggers'. Hands on: send to entry 7 and to epic 8's stop; propose_archive to epic 6.

## Requirements

Verify: A fired trigger's send reaches the scripted switchboard participant once with its action key, mark changes the lane's state through the writer rules, propose_archive opens one archive item, and a trigger naming any other action is refused.

## References

- parent — _bmad-output/initiative-orchestration-control-plane/tickets/epic-triggers/epic-triggers.md

## Notes

- propose_archive opens epic 6's one archive item kind (entry 6.3), deduplicated per lane, never a second item.
