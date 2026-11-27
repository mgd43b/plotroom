---
title: Local Helm end-to-end acceptance evidence
type: chore
ticket: 7
created: '2026-11-26'
status: built
baseline_revision: '731e07b2c72f06582124eac780a1a4ac6569a9e0'
---

## Scope

Record the passing local evidence for #127 on the exact main revision. The kind suite exercises the installation implemented for #121. #126 continues to require green Helm CI for any cleanup changes. The epic's other requirements and the publishing deferral remain in effect.

## Verification

| Epic Done-when check | Recorded evidence |
| --- | --- |
| Install on kind with one replica, Recreate strategy, durable storage, and readiness | The Helm job lints and renders the chart, builds and loads the local image, and calls `deploy/helm/smoke.py`. It installs the chart, asserts one `Recreate` replica and a Bound `ReadWriteOnce` PVC, and checks HTTP 200 at the project's `/readyz` endpoint. The fixture event survives pod replacement. |
| MCP access against the chart | The smoke calls `snapshot()` through the authenticated Python MCP SDK and streamable HTTP `/mcp`. The same job runs `deploy/helm/bridge_smoke.py`, which checks an SDK snapshot with the scoped Host and completes the outside-kind bridge round trip. |
| Real clients covered separately | Completed #123 records actual Claude Code and Codex snapshots and the linked real Codex bridge witness. [Recorded evidence](https://github.com/mgd43b/plotroom/pull/188) |

The [exact Helm job](https://github.com/mgd43b/plotroom/actions/runs/45925376913/job/140265343705) passed on attempt 1. This evidence covers the bound RWO claim and persistence in kind. It does not attest a hosted PV backend, publish an OCI chart, or alter the App-secret acceptance in #122.
