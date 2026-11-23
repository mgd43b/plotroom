---
id: 1
type: story
title: "Tracer: brief() fills a lane brief from the ticket and current claims"
parent: epic-briefs-and-checklists
covers: [R1]
after: [5.1, 1.13]
hitl: false
risk: medium
tracker_id: "95"
remote: "https://github.com/mgd43b/plotroom/issues/95"
tracker_status: done
---

# Tracer: brief() fills a lane brief from the ticket and current claims

## Description

The thinnest path: brief(work_item, worker) (orchestrator only) returns a brief from a template in config, filled with the work item's full text (its file or issue body, read through the work-item source), the lane's own claims as its paths (none before lane_start), a don't-touch section from other lanes' current claims, the plan shape, the expected RESULT shape and the standing rules listed in config; plotroom only returns text, and the orchestrator edits and sends it.

## Requirements

Verify: For a mirrored ticket and a worker, brief() returns the template filled with the ticket's full text, the claims of two other lanes under don't-touch and the configured standing rules, and a worker token's brief() is refused.

## References

- parent — _bmad-output/initiative-orchestration-control-plane/tickets/epic-briefs-and-checklists/epic-briefs-and-checklists.md
