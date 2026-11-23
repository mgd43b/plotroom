---
id: 1
type: story
title: "Tracer: merge_gate(pr) judges checks like the clerk's gate"
parent: epic-merge-controller
covers: [R1, R2]
after: [2.3, 2.14]
hitl: false
risk: high
tracker_id: "63"
remote: "https://github.com/mgd43b/plotroom/issues/63"
tracker_status: backlog
---

# Tracer: merge_gate(pr) judges checks like the clerk's gate

## Description

The thinnest path: one pure gate function over (projection, freezes, config) with a per-rule registry later entries append to, and merge_gate(pr) for orchestrator and clerk tokens returning the verdict, reason codes mapped to safemerge's exit classes (refused, stale, keywords, base red) and the projection's as_of. Rule 1 as in safemerge.sh and lib.sh: a required minimum check count of at least 1 with no default (config fails closed), only the latest run per check name counts, none pending (an ignored check still pending refuses), none failing outside the ignore list, canceled and skipped checks pass; a projection that is stale or not complete for the PR refuses. An orchestrator calling merge_gate on the test repository's PR sees the verdict with its reasons and as_of. Hands on: the registry and gate function to every later entry.

## Requirements

Verify: Table-driven tests built from safemerge.sh's rule-1 cases give the same verdict kind and first reason, including fewer checks than the minimum, a rerun that does not inflate the count, a pending ignored check and a missing minimum in config; merge_gate on a test PR returns the verdict with as_of.

## References

- parent — _bmad-output/initiative-orchestration-control-plane/tickets/epic-merge-controller/epic-merge-controller.md
- .claude/skills/clerk/scripts/safemerge.sh
- .claude/skills/clerk/scripts/lib.sh
- architecture-orchestration-control-plane/architecture-orchestration-control-plane.md#ad-11

## Notes

- Decision: 2026-11-22, the owner chose the clerk's gate as the reference spec ('Copy the clerk's gate'); where safemerge.sh, lib.sh or sweep.sh settle a detail, the gate copies it.
- Check beyond its criteria: with a revoked PAT the projection goes stale and every verdict refuses, never passes.
