---
id: 14
type: story
title: "PR updates, restarts and rate limits"
parent: epic-one-inbox
covers: [R1]
after: [2]
hitl: false
risk: medium
tracker_id: "32"
remote: "https://github.com/mgd43b/plotroom/issues/32"
tracker_status: backlog
---

# PR updates, restarts and rate limits

## Description

The poller adds pushes to a PR's head as new facts, survives a server restart mid-poll without losing or repeating a fact, and backs off on GitHub's rate limit before resuming from its cursor.

## Requirements

Verify: Pushing to a PR moves its head SHA in the projection once; killing the server mid-poll and restarting it produces no missing or duplicate events; a simulated rate-limit response pauses and then resumes polling from the cursor.

## References

- parent — _bmad-output/initiative-orchestration-control-plane/tickets/epic-one-inbox/epic-one-inbox.md
