---
id: 17
type: bug
title: "Recognize legacy Commit Status contexts in the merge gate"
parent: epic-merge-controller
covers: [R1, R2]
after: [1, "2.3"]
refined: true
hitl: false
risk: medium
tracker_id: "232"
remote: "https://github.com/mgd43b/plotroom/issues/232"
tracker_status: done
---

# Recognize legacy Commit Status contexts in the merge gate

## Description

The product gate evaluates Check Runs only. Repositories that also report legacy Commit Status contexts can receive a different Rule 1 verdict from the clerk.

## Requirements

Use bounded read-only evidence from the configured repository and exact head. A current legacy pending context refuses, and an unignored failure or error refuses. Cover success, supersession, minimum and ignore handling, and a complete empty legacy set. Preserve producer-safe Check Runs and test same-name collisions across sources so failure remains visible and a legacy context cannot authenticate App review. Incomplete, malformed, stale or denied required status evidence keeps the result refused. Verify advisory, live and execution-time agreement with provider fixtures. Keep legacy evidence separate from JUnit, flake attribution, Kodus evidence, and the main Actions verdict.

## References

- parent — _bmad-output/initiative-orchestration-control-plane/tickets/epic-merge-controller/epic-merge-controller.md, R1 and R2
- routed finding — https://github.com/mgd43b/plotroom/pull/155

## Verification

The verified implementation pairs current legacy evidence with Check Runs, independently re-reads both at confirmation, and preserves check producers and trusted-App attribution.
