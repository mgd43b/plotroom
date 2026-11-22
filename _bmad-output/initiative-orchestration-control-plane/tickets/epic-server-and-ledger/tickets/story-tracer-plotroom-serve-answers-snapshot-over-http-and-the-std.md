---
id: 2
type: story
title: "Tracer: plotroom serve answers snapshot() over HTTP and the stdio proxy"
parent: epic-server-and-ledger
covers: [R1, R4, R8, R11]
after: []
hitl: false
risk: medium
tracker_id: "13"
remote: "https://github.com/mgd43b/plotroom/issues/13"
tracker_status: backlog
status: built
---

# Tracer: plotroom serve answers snapshot() over HTTP and the stdio proxy

## Description

The thinnest path through transport, store and migrations, plus shared setup: a uv package with ruff, pyright, pytest and the core dependency check in CI on 3.14 and 3.15; the TOML and PLOTROOM_* config loader with the project registry; Alembic with the single-head check; the {ok, data} error envelope; JSON logging. plotroom serve takes the data-directory lock, checks the SQLite floor, migrates one project file and serves an empty snapshot() over stateless HTTP; a thin plotroom connect --stdio forwards to it. Hands on: its empty snapshot() is replaced in entry 6.

## Requirements

Verify: In CI, an MCP SDK client calls snapshot() over HTTP and through plotroom connect --stdio and gets an empty board, and a second plotroom serve on the same directory exits with the lock error.

## References

- parent — _bmad-output/initiative-orchestration-control-plane/tickets/epic-server-and-ledger/epic-server-and-ledger.md
- architecture-orchestration-control-plane/architecture-orchestration-control-plane.md#ad-3

## Notes

- Built: https://github.com/mgd43b/plotroom/pull/28
