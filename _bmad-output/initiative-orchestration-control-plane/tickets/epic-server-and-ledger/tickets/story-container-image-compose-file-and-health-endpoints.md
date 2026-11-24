---
id: 10
type: story
title: "Container image, compose file and health endpoints"
parent: epic-server-and-ledger
covers: [R8]
after: [2, 4, 5]
hitl: false
risk: medium
tracker_id: "22"
remote: "https://github.com/mgd43b/plotroom/issues/22"
tracker_status: done
status: built
---

# Container image, compose file and health endpoints

## Description

A uv-based Debian slim image with a SQLite that meets the floor; a compose file with a volume for the data directory, the instance token file mounted and allowed Host names configured; /healthz and per-project /readyz; the first orchestrator token issued with docker compose run plotroom token issue against the volume; the Dockerfile built in CI. Hands on: the image and health endpoints to epic 11.

## Requirements

Verify: docker compose up serves snapshot() to an HTTP client holding an issued token with /readyz passing, and after a container restart the same token still works and a recorded fixture event is still there.

## References

- parent — _bmad-output/initiative-orchestration-control-plane/tickets/epic-server-and-ledger/epic-server-and-ledger.md

## Notes

- Built: https://github.com/mgd43b/plotroom/pull/183
