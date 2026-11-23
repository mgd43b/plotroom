---
id: 9
type: story
title: "Claude intake through ingest(), the SessionReader port and inbound-message items"
parent: epic-one-inbox
covers: [R6, R7, R10]
after: [1, 7, 1.4, 1.5, 1.6, 1.8, 1.9]
hitl: false
risk: medium
tracker_id: "43"
remote: "https://github.com/mgd43b/plotroom/issues/43"
tracker_status: backlog
---

# Claude intake through ingest(), the SessionReader port and inbound-message items

## Description

ingest() appends only claude.* events (turn reports, session messages) from orchestrator, clerk and worker tokens, a worker's forced to its current lane; claude.turn_report takes an optional turn_index; every turn report, from ingest or from the local reader, records both its turn_index (when known) and a digest of its report text, and a later report for the same session matching either is a duplicate, so a turn seen both ways is stored once in either arrival order; the SessionReader port is defined, shaped by entry 1's finding, so a local reader can write the same events as well; an unhandled Codex message (entry 7), Claude session message or Claude turn report raises an inbound-message item that ack closes and that wakes plotroom watch. Hands on: the port to entry 18.

## Requirements

Verify: A turn report sent through ingest twice is stored once, with or without turn_index; a worker token's report lands on its own lane whatever lane it names, and a github.* kind from it is refused; a turn report and a switchboard message each raise an item that wakes a running plotroom watch and closes on ack.

## References

- parent — _bmad-output/initiative-orchestration-control-plane/tickets/epic-one-inbox/epic-one-inbox.md
