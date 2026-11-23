---
id: 4
type: story
title: "Event leaves, deploys, idle lanes and every-triggers"
parent: epic-triggers
covers: [R1, R3]
after: [3, 2.7, 2.9, 2.11]
hitl: false
risk: medium
tracker_id: "58"
remote: "https://github.com/mgd43b/plotroom/issues/58"
tracker_status: backlog
---

# Event leaves, deploys, idle lanes and every-triggers

## Description

Adds message_from(worker) (an event newer than the trigger's last fired seq), deploy_finished(env, sha) over deploy events, lane_idle(lane) (no event on the lane for a configured number of minutes, judged on ticks), and every triggers firing on each false-to-true transition.

## Requirements

Verify: An every trigger on message_from fires once per new message and not on replay; deploy_finished fires on a matching deploy event; lane_idle fires once after the configured quiet time under a fake Clock and again only after the lane was active.

## References

- parent — _bmad-output/initiative-orchestration-control-plane/tickets/epic-triggers/epic-triggers.md
