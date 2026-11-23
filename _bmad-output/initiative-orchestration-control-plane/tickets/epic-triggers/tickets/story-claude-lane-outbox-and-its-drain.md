---
id: 7
type: story
title: "Claude-lane outbox and its drain"
parent: epic-triggers
covers: [R6]
after: [6, 1.5, 1.9]
hitl: true
risk: high
tracker_id: "60"
remote: "https://github.com/mgd43b/plotroom/issues/60"
tracker_status: backlog
---

# Claude-lane outbox and its drain

## Description

A send to a Claude lane becomes an outbox action; the drain session named in the project's config wakes through wait(), takes rows with an outbox_lease command and completes them with outbox_done(act_id), both declared for the drain role in the writer-rule table, and delivers each with native session messaging; a lease that expires is offered again; each message carries its action key. Builds the drain stand-in entry 10 uses; a person runs one real drain session delivering to a real Claude lane. Hands on: the Claude channel to epic 8's stop.

## Requirements

Verify: With a drain token, a fired send to a Claude lane is leased once, outbox_done completes it, an expired lease is offered again with the same action key, a drain token is refused every command but wait(), outbox_lease and outbox_done, and a real drain session delivers one message to a real Claude lane.

## References

- parent — _bmad-output/initiative-orchestration-control-plane/tickets/epic-triggers/epic-triggers.md

## Notes

- Check beyond its criteria: two drain sessions running by mistake never deliver the same row twice within a lease.
- Decision: 2026-11-22, leasing is its own outbox_lease command; wait() stays read-only and only wakes the drain.
