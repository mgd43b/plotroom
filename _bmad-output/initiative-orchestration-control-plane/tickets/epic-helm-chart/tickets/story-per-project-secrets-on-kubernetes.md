---
id: 2
type: story
title: "Per-project secrets on Kubernetes"
parent: epic-helm-chart
covers: [R1]
after: [1, 2.6, 2.2]
hitl: false
risk: medium
tracker_id: "122"
remote: "https://github.com/mgd43b/plotroom/issues/122"
tracker_status: done
---

# Per-project secrets on Kubernetes

## Description

The chart mounts per-project secrets (GitHub App key, PAT, embedder key) from Kubernetes Secrets as the secret files plotroom reads, and never renders a secret value into the ConfigMap.

## Requirements

Verify: A kind install with a Secret for an App key polls the test repository with the App, and helm template output contains no secret value.

## References

- parent — _bmad-output/initiative-orchestration-control-plane/tickets/epic-helm-chart/epic-helm-chart.md

## Notes

### 2026-11-27 — Local kind acceptance

In an owner-authorized run on the [verified PR #215 merge](https://github.com/mgd43b/plotroom/actions/runs/46007832740), a fresh local kind install mounted the selected GitHub App Secret key read-only with mode `0440`. The non-root daemon read the mounted key. `/readyz` and an authenticated MCP snapshot succeeded. The fresh GitHub projection reported available at generation 1 and matched the dedicated fixture repository's two existing PRs, zero open PRs, and unchanged default branch.

The Helm render, live ConfigMap, and server logs were scanned for PEM variants, JWTs, and bearer values. The scan found no credential material. The disposable cluster and temporary resources were removed. Hosted rollout, CI secret setup, and publishing remain deferred.
