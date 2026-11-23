---
id: 1
type: story
title: "Tracer: a budget raises items at 50% and 80% of its cap"
parent: epic-guardrails
covers: [R1, R2]
after: [1.6, 1.7]
hitl: false
risk: medium
tracker_id: "101"
remote: "https://github.com/mgd43b/plotroom/issues/101"
tracker_status: backlog
---

# Tracer: a budget raises items at 50% and 80% of its cap

## Description

The thinnest path: budget_open(run, lane, cap_usd, provider, key_scope) (orchestrator only, the key scope recorded by name), a UsageMeter port with a fixture meter reporting cumulative spend per (meter, run) with an as-of time, a budget taking the latest reading and never summing, and once-only items at 50% and 80% keyed budget:<bud_id>:<50|80>; budget_status(run?) reports spend against cap.

## Requirements

Verify: With the fixture meter, readings crossing 50% and 80% each open one item, repeated and overlapping readings open nothing new, and budget_status shows the latest spend against the cap and the run's lane.

## References

- parent — _bmad-output/initiative-orchestration-control-plane/tickets/epic-guardrails/epic-guardrails.md
