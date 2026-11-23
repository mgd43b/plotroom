---
id: 1
type: story
title: "Tracer: decide() records the human's exact words"
parent: epic-human-queue
covers: [R1]
after: [1.5, 2.5]
hitl: false
risk: medium
tracker_id: "82"
remote: "https://github.com/mgd43b/plotroom/issues/82"
tracker_status: done
---

# Tracer: decide() records the human's exact words

## Description

The thinnest path: decide(who, exact_words, where_ref, governs[], settles[]) (orchestrator only) stores the words byte for byte with who, where and when, the tickets it governs and the asks it settles; words cited from a GitHub comment must appear in that comment's projected body, and its projected author must be both who and the configured human_login, and a comment not yet in the projection returns a retryable unavailable error; decisions(governs?) lists them; the project config gains its human_login key. Hands on: decisions to entries 4 and 6 and to epic 10's search; human_login to entries 3 and 6.

## Requirements

Verify: Words quoted from a test comment are stored byte for byte with the tickets they govern and decisions(governs) finds them, words not in the comment, and words from a comment by another author, are refused, a just-posted comment not yet polled returns unavailable, a decision citing a chat source is stored as given, and a worker token's decide is refused.

## References

- parent — _bmad-output/initiative-orchestration-control-plane/tickets/epic-human-queue/epic-human-queue.md
