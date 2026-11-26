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
refined: true
risk: high
---

# Server and ledger

## Description

One plotroom daemon per data directory serves the ledger over a stdio proxy and stateless HTTP with per-project role tokens; the event log, unit of work, action runner, ticks and a local-path ticket-tree mirror are in place; an orchestrator starts lanes for configured workers, rebuilds from snapshot() and next(), acks items and wakes through plotroom watch; it ships as a package, an image and a compose file.

## Outcome

An orchestrator can be restarted or compacted mid-run and rebuild the board from two calls, against a local daemon or a hosted container.

## Requirements

- R1 (parent R1, R27): Ledger state persists in one SQLite file per project under one owning daemon; a restart loses nothing, and no call reaches another project's file.
- R2 (parent R2): `snapshot()` summarizes the whole board and lists items by rank within its token budget, stating how many it left out.
- R3 (parent R3): `next()` returns ranked items of the kinds this epic raises (failed action, lane waiting on a reply, idle worker with unblocked work, watcher not running), each with context and suggested actions; later epics add kinds to the same registry.
- R4 (parent R4, R24): One MCP tool set (`snapshot`, `next`, `ack`, `wait`, `lane_start`, `lane_update`, `lane_ready`, `lane_archive`) is served over stateless HTTP and through `plotroom connect --stdio`.
- R5 (parent R8): `wait(cursor, timeout)` returns on the first new visible row or at a 240-second cap with progress notifications, and `plotroom watch` loops it until a row arrives.
- R6 (parent R9): `ack(item)` closes an event-backed item and snoozes a state-backed one; a second `ack` is a no-op; upstream acknowledgment belongs to epic 2.
- R7 (parent R22): Every command is declared in one writer-rule table by role; an undeclared call, and a worker calling an orchestrator-only command, are refused.
- R8 (parent R24): Locally, one daemon owns a data directory under an OS lock and a second refuses to start; hosted, the image and compose file run it with `/readyz` passing.
- R9 (parent R1, R3): Workers are declared in config; lanes record their work items, branch, state and `waiting_on`; work items are mirrored read-only from a local ticket-tree path.
- R10 (decision 2026-11-22): A release publishes wheels to PyPI and an image to ghcr.io through release-please, only after CI passes.
- R11 (decision 2026-11-22): CI runs ruff, pyright, pytest, the core dependency check and the transport contract test on Python 3.14 and 3.15 with uv-managed Python.
- R12 (decision 2026-11-22): `plotroom token issue` and `revoke` manage hashed tokens; locally, the issued token is kept in a 0600 tokens file under `~/.config/plotroom/` that the stdio proxy reads.

## Constraints

- From the initiative: C2 (no credentials in records), C5 (replay and keyed actions), C7 (event text is data), C8 (token budgets), C14 (one build for local and hosted).
- From the spine: AD-1 to AD-9, AD-12 to AD-14 and AD-18 bind this epic's stories.

## Done when

- `plotroom serve` holds the data-directory lock, and a second server refuses to start.
- A failed action opens a next item, and `ack` closes or snoozes it.
- Through both `plotroom connect --stdio` and HTTP, an orchestrator token starts a lane for a configured worker, and a worker token is refused an orchestrator-only command.
- After a restart, `snapshot()` and `next()` return the same board, and `plotroom watch` wakes on a new item.
- The image and compose file run the server with one project file on a volume, with `/readyz` passing.
- CI runs lint, type checks, tests, the dependency check and the transport contract test on Python 3.14 and 3.15.
- A first release publishes the wheel to PyPI and the image to ghcr.io after CI passes.

## Boundaries

- Out: every inbox adapter and `ingest` (epic 2), triggers (epic 3), the gate (epic 4), the Helm chart (epic 11).
- Owns the platform baseline: scaffold, CI, packaging, the SQLite floor check, the event log and unit of work, the `actions` table and action runner, `core.tick`, the registries (event kinds, next-item kinds, writer rules), the local-path ticket-tree mirror, `ack` for next items, the stdio proxy, `wait()` and `plotroom watch`; the spine's client spikes and pre-migration backups land here.
- Hands on: the tool surface and registries every later epic adds entries to; the tree mirror to epics 2 and 5.

## References

- parent — _bmad-output/initiative-orchestration-control-plane/initiative-orchestration-control-plane.md, Requirements R1, R2, R3, R4, R8, R9, R22, R24, R27
- architecture — _bmad-output/initiative-orchestration-control-plane/architecture-orchestration-control-plane/architecture-orchestration-control-plane.md

## Notes

- Decision: 2026-11-22, refined with the owner: release to PyPI and ghcr.io through release-please; pyright for types; four next-item kinds in this epic; local tokens in a 0600 file under `~/.config/plotroom/`.
- Decision: 2026-11-22, tracer bullet is entry 2 (transport, store and migrations with shared setup); the write path, auth and the container are entries 4, 5 and 10 because each is a session of its own. Entry 3, the least certain, follows it.
- Decision: 2026-11-22, the owner kept the epic whole at 16 entries and added a closing end-to-end suite (entry 16) after the refactor sweep (entry 12); the sweep does not wait on the clerk spike.
- Decision: 2026-11-22, entries built in parallel each add Alembic revisions on one linear history; the later one to merge re-parents its revision onto main's head.
- Decision: 2026-11-22, `plotroom token issue` writes the instance token file directly under a file lock (it is instance config, not a project file), so the first orchestrator token needs no running server.
- Hands on: entry 4 to epic 2 (event log), entry 7 and entry 5 to epic 3 (action runner, drain role), entry 13 to epics 2 and 5 (WorkItemSource port), entry 10 to epic 11 (image and health endpoints), entry 1 to epic 9 (what the clerk's ledger holds).
- Decision: 2026-11-22, Alembic revisions live in the package at `src/plotroom/adapters/store_sqlite/migrations/`, with `alembic.ini` at the repository root, so the wheel carries them (found in the tracer's PR #28); every entry that adds a revision puts it there.
- Decision: 2026-11-22, serve's lock refusal exits with code 4, not 3, because uvicorn exits 3 on a failed start (lane #14, PR #51); entry 15's contract test checks it.
- Decision: 2026-11-22, a config port of 0 now means "discover": the daemon binds any free port and publishes `{pid, port}` in `plotroom.port` in its data directory, and `connect --stdio` reads it back only while the data-directory lock is held by that pid, then adopts the URL (ticket 18, issue #91). Tests start every daemon on port 0 and keep no helper that picks a port and releases it; a fixed port behaves as before.
- Implementation (PR #134, issue #16): Local daemon discovery reads the bound IP address, PID, and port from the daemon's port file. The local bearer path uses the lock owner's published host. A configured port of 0 adopts the published port. A configured nonzero port must match the published port. A config host of `localhost` retains the `localhost` Host header. A hostless legacy record is accepted only for port-0 discovery without a token.
- Decision: 2026-11-22, `plotroom serve --log-file PATH` rotates its log by size (log_max_bytes, log_backups); a daemon started on demand by the stdio proxy gets `--log-file <data_dir>/plotroom-serve.log`, and a serve started by hand logs to stdout (#90, PR #132).
- Real-client bare-401 check for #16 (2026-11-24, main `6990eeea`). The direct HTTP `/mcp` listener returned HTTP 401 with an empty body and no `WWW-Authenticate` header.
  - Codex CLI 0.169.0 showed `failed (0 tools)` in `/mcp`. Initialization reported `unexpected server response: HTTP 401`. Opening `/mcp` triggered a second connection attempt.
  - Claude Code 2.1.325 marked the server failed in `/mcp`, retried with the legacy protocol, and logged `Dynamic Client Registration rejected (HTTP 401)`.
  - Both clients remained usable after the connection failed. Neither displayed a sign-in prompt during the check.
- Native acceptance dependency: #25 supplies the CI-gated first PyPI wheel and GHCR image publication required by R10 and the final Done-when criterion.
