---
id: 18
type: bug
title: "Keep merge gate detail evidence fresh across bounded polling"
parent: epic-merge-controller
covers: [R1, R2]
after: [1, "2.14"]
refined: true
hitl: false
risk: medium
tracker_id: "233"
remote: "https://github.com/mgd43b/plotroom/issues/233"
tracker_status: done
---

# Keep merge gate detail evidence fresh across bounded polling

## Description

The per-cycle pull-detail budget can leave an active PR with periodically stale gate evidence even when all provider reads succeed.

## Requirements

Prevent periodic stale refusals caused by scheduler rotation within supported capacity. Exercise seventeen active PRs at the default two-interval freshness ratio and verify how the bounded schedule meets the configured freshness requirement or reports insufficient capacity. Preserve repository, head and pull-version binding, fairness across active and terminal pulls, and bounded work per cycle. Verify head changes, temporary failures, cooldown and restart. Overload and missing evidence still refuse. Preserve the freshness limit, timestamps, other gate behavior and the bounded on-demand recheck.

## References

- parent — _bmad-output/initiative-orchestration-control-plane/tickets/epic-merge-controller/epic-merge-controller.md, R1 and R2
- routed finding — https://github.com/mgd43b/plotroom/pull/155

## Verification

The verified implementation caps active and terminal work, honors the existing freshness window and cooldown, and refuses project merges above capacity. Rule 4 retains its own eligibility checks.
