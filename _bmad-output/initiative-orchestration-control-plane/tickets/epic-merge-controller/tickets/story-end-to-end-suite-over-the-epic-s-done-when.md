---
id: 10
type: story
title: "End-to-end suite over the epic's Done when"
parent: epic-merge-controller
covers: [R1, R2, R3, R4, R5, R6, R7]
after: [9]
hitl: false
risk: low
tracker_id: "75"
remote: "https://github.com/mgd43b/plotroom/issues/75"
tracker_status: done
---

# End-to-end suite over the epic's Done when

## Description

One suite runs every Done-when check of this epic against a real daemon and fixture PRs on a test repository kept apart from epics 2 and 3's suites (one PR per rule and reason), and compares the gate with safemerge.sh run under ORCH_DRY=1 on the same PRs (same verdict kind, and safemerge's first reason among the gate's). The fix label, the deploy window, the one-open-PR rule and judging more than 100 fully paginated threads are plotroom's own and are not compared.

## Requirements

Verify: The suite passes in CI, one test per Done-when line.

## References

- parent — _bmad-output/initiative-orchestration-control-plane/tickets/epic-merge-controller/epic-merge-controller.md

## Notes

- Once epic 9 retires the hand-run clerk (entry 9.4), the comparison runs against a pinned copy of safemerge.sh and lib.sh under tests/fixtures.
