---
id: 4
type: story
title: "Decisions settle asks and tickets"
parent: epic-human-queue
covers: [R1, R2]
after: [1, 3]
hitl: false
risk: low
tracker_id: "86"
remote: "https://github.com/mgd43b/plotroom/issues/86"
tracker_status: done
---

# Decisions settle asks and tickets

## Description

A decision's settles[] and governs[] link it to asks and tickets; a settled ask stops being flagged, and asks(), decisions() and a ticket's decisions show the links both ways.

## Requirements

Verify: Recording a decision that settles an ask closes its stale item, and both lists show the ask link and the ticket link.

## References

- parent — _bmad-output/initiative-orchestration-control-plane/tickets/epic-human-queue/epic-human-queue.md
