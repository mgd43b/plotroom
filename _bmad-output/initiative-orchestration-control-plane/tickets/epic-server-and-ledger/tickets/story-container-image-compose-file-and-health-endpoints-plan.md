---
title: Container image, Compose file, and health endpoints
type: feature
ticket: 10
created: '2026-11-24'
status: in-review
baseline_revision: '7409917d0d93c76eab1d7f00ac3fb3aebf886de7'
route: full
route_source: auto
risk: medium
review: thorough
review_source: pinned
---

<frozen-after-approval reason="approved local container scope">

## Intent

Run the existing daemon in a Debian slim image with uv-managed Python and a compatible SQLite. Keep project files and instance token hashes on one named volume. Expose host-checked liveness and project readiness separately from authenticated MCP.

## Boundaries & Constraints

Use an isolated local Compose project and volume and loopback port publishing. Keep both transports and role authorization intact. Build the image in CI.

</frozen-after-approval>

## Code Map

- The MCP adapter in src/plotroom/adapters/mcp/server.py owns Host and bearer verification and the HTTP endpoint.
- src/plotroom/app/serve.py supplies configured projects and the ledgers that are ready.
- Dockerfile and compose.yaml package the existing CLI. The CI workflow builds the same Dockerfile.

## Tasks & Acceptance

- Given a fresh volume, when the daemon and offline token command share it, then a bearer-authenticated MCP client can record a fixture event and read its project.
- Given a container restart, when the client reuses the bearer, then the fixture event is still present and the project reports ready.
- Given an unready project, when checking readiness and liveness, then readiness returns 503 and liveness remains available.
- Given a foreign Host, when checking a probe, then the existing Host check rejects it.

## Implementation Notes

The Dockerfile validated the uv-managed Python's SQLite floor on build. A local Compose startup
on a disposable named volume passed liveness, readiness, authenticated snapshot, and a persisted
fixture decision event using the same bearer after an actual restart. The targeted HTTP and
low-SQLite probes also passed. Independent review will cover the combined batch.

## Plan Change Log

## Review Triage Log

## Verification

- Run targeted HTTP tests and a real Compose fixture with build, token issuance, authenticated MCP, readiness, and restart.
