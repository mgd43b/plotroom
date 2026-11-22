---
tracker_id: "2"
remote: https://github.com/mgd43b/plotroom/issues/2
type: epic
title: "One inbox"
parent: initiative-orchestration-control-plane
covers: [R7, R9, R24, R26]
sources: []
companions:
  - _bmad-output/initiative-orchestration-control-plane/architecture-orchestration-control-plane/architecture-orchestration-control-plane.md
after: [epic-server-and-ledger]
refined: false
risk: high
---

# One inbox

## Description

GitHub (PRs, checks on main including image builds, review threads, issues and comments), the Codex switchboard (directly or through plotroom bridge), Claude session reports and deploy status arrive as typed, deduplicated events in one inbox, GitHub state is kept in one projection, the ticket tree is mirrored from the default branch, and the switchboard is acknowledged upstream once each event commits.

## Outcome

Every signal the orchestrator used to collect from four channels arrives in one place, once, behind one wake signal.

## Done when

- A merged PR, a CI completion and a review thread on a test repository each appear once as typed `github.*` events and in the projection.
- A Codex message arrives through the switchboard, locally and through `plotroom bridge` against a hosted instance, and is acknowledged upstream once it commits.
- A Claude turn report sent through `ingest` twice is stored once.
- The session reader spike's answer is recorded, and R26 is met by `ingest` alone or with the reader.

## Boundaries

- Out: reacting to events (triggers in epic 3, the gate in epic 4).
- Owns the GitHub projection (PRs, checks on main including image builds, review threads, issues and comments), the switchboard reader and `plotroom bridge` (both directions), upstream acknowledgment, the roles allowed to call `ingest`, the `SessionReader` port and its spike, the default-branch tree reader and the deploy status reader.
- Out: provider usage (epic 8).
- Hands on: the projection to epics 3, 4, 5 and 7; switchboard delivery to epic 3.

## References

- parent — _bmad-output/initiative-orchestration-control-plane/initiative-orchestration-control-plane.md, Requirements R7, R9, R24, R26
- architecture — _bmad-output/initiative-orchestration-control-plane/architecture-orchestration-control-plane/architecture-orchestration-control-plane.md

## Notes

- Waits on epic-server-and-ledger because: the event log, unit of work and MCP tool surface.
