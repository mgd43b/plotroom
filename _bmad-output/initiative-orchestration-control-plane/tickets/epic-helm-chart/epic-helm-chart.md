---
tracker_id: "11"
remote: https://github.com/mgd43b/plotroom/issues/11
type: epic
title: "Helm chart"
parent: initiative-orchestration-control-plane
covers: [R24]
sources: []
companions:
  - _bmad-output/initiative-orchestration-control-plane/architecture-orchestration-control-plane/architecture-orchestration-control-plane.md
after: [epic-server-and-ledger, epic-one-inbox]
refined: false
risk: low
---

# Helm chart

## Description

A Helm chart deploys plotroom on Kubernetes as one replica with the Recreate strategy and a block volume, and the same Claude Code and Codex clients that use a local instance work against it, Codex through the bridge.

## Outcome

Anyone can run plotroom on Kubernetes from a chart, which closes the initiative's Kubernetes check.

## Done when

- `helm install` on a test cluster runs one replica with the Recreate strategy and a block volume, with `/readyz` passing.
- The MCP clients that work against a local daemon work against the chart's instance.

## Boundaries

- Out: several replicas (needs the Postgres adapter, a later initiative).

## References

- parent — _bmad-output/initiative-orchestration-control-plane/initiative-orchestration-control-plane.md, Requirements R24
- architecture — _bmad-output/initiative-orchestration-control-plane/architecture-orchestration-control-plane/architecture-orchestration-control-plane.md

## Notes

- Waits on epic-server-and-ledger because: the container image and health endpoints.
- Waits on epic-one-inbox because: plotroom bridge for Codex workers.
