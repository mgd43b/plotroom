---
id: 14
type: bug
title: "The clerk's gate merges without completed review evidence"
parent: epic-merge-controller
covers: []
after: []
refined: false
hitl: false
risk: medium
tracker_id: "141"
remote: "https://github.com/mgd43b/plotroom/issues/141"
tracker_status: done
---

# The clerk's gate merges without completed review evidence

## Description

The copied bootstrap clerk gate must require completed review evidence from the configured Kody GitHub App for every PR. Use completed review on the current head, or the completed opening review when the configured cadence skips a later head and all review threads are answered and resolved. The gate must still check CI on the current pinned head.

## Requirements

Verify: Use dry-run fixtures to confirm that the gate refuses missing or unreadable evidence, progress-only evidence, a skip without a completed opening review, and unresolved review threads. A completed Kody review with no findings passes. A completed opening review plus a valid later cadence skip passes after review threads are answered and resolved, with current-head CI and main checks passing.

## References

- parent — _bmad-output/initiative-orchestration-control-plane/tickets/epic-merge-controller/epic-merge-controller.md
- .claude/skills/clerk/scripts/safemerge.sh
- .claude/skills/clerk/scripts/lib.sh
- coordination/README.md

## Notes

- This ticket applies to the copied bootstrap clerk gate. Route a corresponding requirement for Plotroom's merge controller through the coordinator for separate scope review.
