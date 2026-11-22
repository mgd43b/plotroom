---
tracker_id: "1"
remote: https://github.com/mgd43b/plotroom/issues/1
type: epic
title: "Server and ledger"
parent: initiative-orchestration-control-plane
covers: [R1, R2, R3, R4, R8, R9, R22, R24, R27]
sources: []
companions:
  - _bmad-output/initiative-orchestration-control-plane/architecture-orchestration-control-plane/architecture-orchestration-control-plane.md
after: []
refined: false
risk: high
---

# Server and ledger

## Description

One plotroom daemon per data directory serves the ledger over a stdio proxy and stateless HTTP with per-project role tokens; the event log, unit of work, action runner, ticks and a local-path ticket-tree mirror are in place; an orchestrator records workers and lanes, rebuilds from snapshot() and next(), acks items and wakes through plotroom watch; it ships as a package, an image and a compose file.

## Outcome

An orchestrator can be restarted or compacted mid-run and rebuild the board from two calls, against a local daemon or a hosted container.

## Done when

- `plotroom serve` holds the data-directory lock, and a second server refuses to start.
- A failed action opens a next item, and `ack` closes or snoozes it.
- Through both `plotroom connect --stdio` and HTTP, an orchestrator token records a worker and a lane, and a worker token is refused an orchestrator-only command.
- After a restart, `snapshot()` and `next()` return the same board, and `plotroom watch` wakes on a new item.
- The image and compose file run the server with one project file on a volume, with `/readyz` passing.
- CI runs lint, type checks, tests, the dependency check and the transport contract test on Python 3.14 and 3.15.

## Boundaries

- Out: inbox adapters other than `ingest` (epic 2), triggers (epic 3), the gate (epic 4), the Helm chart (epic 11).
- Owns the platform baseline: scaffold, CI, packaging, the SQLite floor check, the event log and unit of work, the `actions` table and action runner, `core.tick`, the registries (event kinds, next-item kinds, writer rules), the local-path ticket-tree mirror, `ack` for next items, the stdio proxy, `wait()` and `plotroom watch`; the spine's transport and packaging spikes and pre-migration backups land here.
- Hands on: the tool surface and registries every later epic adds entries to; the tree mirror to epics 2 and 5.

## References

- parent — _bmad-output/initiative-orchestration-control-plane/initiative-orchestration-control-plane.md, Requirements R1, R2, R3, R4, R8, R9, R22, R24, R27
- architecture — _bmad-output/initiative-orchestration-control-plane/architecture-orchestration-control-plane/architecture-orchestration-control-plane.md
