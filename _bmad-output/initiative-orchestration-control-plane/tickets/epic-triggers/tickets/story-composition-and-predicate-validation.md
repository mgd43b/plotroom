---
id: 11
type: story
title: "Composition and predicate validation"
parent: epic-triggers
covers: [R1, R2, R3]
after: [8]
hitl: false
risk: medium
tracker_id: "55"
remote: "https://github.com/mgd43b/plotroom/issues/55"
tracker_status: done
---

# Composition and predicate validation

## Description

Adds all_of and any_of over registered leaves, and refuses a predicate with free text or an unknown leaf with the reason.

## Requirements

Verify: all_of(pr_merged(a), pr_merged(b)) fires once when the second PR merges whichever merges first, also across a server restart between the merges; any_of fires on the first leaf; a predicate with a free-text leaf and one with an unknown leaf are each refused as invalid.

## References

- parent — _bmad-output/initiative-orchestration-control-plane/tickets/epic-triggers/epic-triggers.md

## Notes

- Built: https://github.com/mgd43b/plotroom/pull/148
