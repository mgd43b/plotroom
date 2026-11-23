---
id: 5
type: story
title: "Exactly once across restarts, and branch-glob binding"
parent: epic-triggers
covers: [R2, R3]
after: [1]
hitl: false
risk: high
tracker_id: "53"
remote: "https://github.com/mgd43b/plotroom/issues/53"
tracker_status: backlog
---

# Exactly once across restarts, and branch-glob binding

## Description

Proves the engine's exactly-once rule: fired state survives restarts and replays; startup does not re-evaluate stored events into new firings; every PR leaf accepts a branch glob, which binds to the first matching PR (by its head branch in the projection) and records that binding so a later match does not rebind.

## Requirements

Verify: Killing the server after a trigger's fire commits and before the action runner executes its action, then restarting, runs the action once with the same action key and does not fire the trigger again; a branch-glob trigger bound to the first matching PR ignores a second one.

## References

- parent — _bmad-output/initiative-orchestration-control-plane/tickets/epic-triggers/epic-triggers.md

## Notes

- Check beyond its criteria: replaying the recorded inputs after a restart opens no second next item and writes no second actions row.
