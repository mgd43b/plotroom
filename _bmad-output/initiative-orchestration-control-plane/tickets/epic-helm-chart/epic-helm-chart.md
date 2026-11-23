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
after: []
refined: true
risk: low
---

# Helm chart

## Description

A Helm chart deploys plotroom on Kubernetes as one replica with the Recreate strategy and a block volume, and the same Claude Code and Codex clients that use a local instance work against it, Codex through the bridge.

## Outcome

Anyone can run plotroom on Kubernetes from a chart, which closes the initiative's Kubernetes check.

## Requirements

- R1 (parent R24): A Helm chart deploys plotroom on Kubernetes as one replica with the Recreate strategy, a block volume, mounted secrets and optional scheduled backups, published as an OCI chart.
- R2 (parent R24): The same Claude Code and Codex clients that use a local instance work against the chart's instance, Codex through plotroom bridge.

## Constraints

- From the initiative: C2, C14 (one build).
- From the spine: AD-3, AD-4, AD-7 and AD-12 bind this epic's stories.

## Done when

- `helm install` on a test cluster runs one replica with the Recreate strategy and a block volume, with `/readyz` passing.
- The MCP clients that work against a local daemon work against the chart's instance.

## Boundaries

- Out: several replicas (needs the Postgres adapter, a later initiative).

## References

- parent — _bmad-output/initiative-orchestration-control-plane/initiative-orchestration-control-plane.md, Requirements R24
- architecture — _bmad-output/initiative-orchestration-control-plane/architecture-orchestration-control-plane/architecture-orchestration-control-plane.md

## Notes

- Waits on epic-server-and-ledger, story by story, for: the container image and health endpoints.
- Waits on epic-one-inbox, story by story, for: plotroom bridge for Codex workers.
- Decision: 2026-11-22, build order 1 (helm install on kind, the tracer), 2, 3, 4, 5 (publishing, which needs the owner), with a refactor sweep (6) and an end-to-end suite (7); this epic closes the initiative's Kubernetes check. Prerequisites are story by story, with no whole-epic gate.
- Decision: 2026-11-22, from the set check: the instance token file lives on the data volume and the first token comes from a one-off Job in the tracer; per-project secrets are mounted Secrets; backups go through a server command to their own volume; a person checks real clients against the chart (entry 3).
