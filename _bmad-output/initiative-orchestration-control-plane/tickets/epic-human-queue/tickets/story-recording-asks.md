---
id: 2
type: story
title: "Recording asks"
parent: epic-human-queue
covers: [R2]
after: [1.5, 2.5]
hitl: false
risk: low
tracker_id: "84"
remote: "https://github.com/mgd43b/plotroom/issues/84"
tracker_status: backlog
---

# Recording asks

## Description

ask(issue, text, options[], comment_id) records an ask the orchestrator posted on the human's issue (a comment not yet polled returns a retryable unavailable error), and asks(stale_only?) lists them; the tool never posts the comment itself (C3). Hands on: asks to epic 10's search.

## Requirements

Verify: An ask recorded against a test comment lists with its options and comment id, and an ask whose comment is not yet in the projection returns unavailable.

## References

- parent — _bmad-output/initiative-orchestration-control-plane/tickets/epic-human-queue/epic-human-queue.md
