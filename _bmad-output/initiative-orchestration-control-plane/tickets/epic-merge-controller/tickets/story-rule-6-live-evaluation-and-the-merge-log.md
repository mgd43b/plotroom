---
id: 13
type: story
title: "Rule 6, live evaluation and the merge log"
parent: epic-merge-controller
covers: [R1, R3]
after: [3, 4, 11, 12]
hitl: false
risk: medium
tracker_id: "70"
remote: "https://github.com/mgd43b/plotroom/issues/70"
tracker_status: backlog
---

# Rule 6, live evaluation and the merge log

## Description

Every verdict is pinned to the head SHA; the gate runs on every relevant event; a verdict is logged in the merge log as an inbox event only when its kind or reasons change for that head, a new head superseding the old; pending and too-few-checks verdicts open no items; branch updates log BRANCH UPDATED.

## Requirements

Verify: On recorded fixtures merge_gate and the live gate give the same verdict for every PR; a new head logs a new verdict and supersedes the old; a branch update logs BRANCH UPDATED; a pending PR opens no item.

## References

- parent — _bmad-output/initiative-orchestration-control-plane/tickets/epic-merge-controller/epic-merge-controller.md
