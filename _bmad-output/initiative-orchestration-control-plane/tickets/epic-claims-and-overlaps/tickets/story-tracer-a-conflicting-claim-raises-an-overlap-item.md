---
id: 1
type: story
title: "Tracer: a conflicting claim raises an overlap item"
parent: epic-claims-and-overlaps
covers: [R1, R4]
after: [1.8]
hitl: false
risk: medium
tracker_id: "76"
remote: "https://github.com/mgd43b/plotroom/issues/76"
tracker_status: done
---

# Tracer: a conflicting claim raises an overlap item

## Description

The thinnest path: claim(lane, globs[], reason) and release(lane, globs[]) by a worker for its own lane or by the orchestrator for any lane; globs are gitignore-style without negation; two claims overlap when one glob's literal prefix (up to its first wildcard) contains the other's, a glob with no slash (which matches at any depth) counting as an empty prefix, judged conservatively; an overlapping claim opens an overlap item when it is made; lane_archive releases the lane's claims. Hands on: claims to entry 2 and to epic 6's briefs.

## Requirements

Verify: A worker claims src/a/** for its lane, another lane's claim of src/a/x.py opens one overlap item naming both lanes, a worker cannot claim for another lane while the orchestrator can claim for lane B, a slashless claim of config.yml and another lane's claim of src/config.yml overlap, a negated glob is refused, and release or archiving the lane clears the item.

## References

- parent — _bmad-output/initiative-orchestration-control-plane/tickets/epic-claims-and-overlaps/epic-claims-and-overlaps.md

## Notes

- Decision: 2026-11-22, the owner chose that a lane's worker claims for its own lane and the orchestrator for any lane ('Lane + orchestrator').
- Decision: 2026-11-22, globs have no negation and claim-against-claim overlap uses literal prefixes, so the check is decidable and errs toward reporting; the tracer starts before the projection exists, and PR diffs come in entry 2.

- Built: https://github.com/mgd43b/plotroom/pull/150
