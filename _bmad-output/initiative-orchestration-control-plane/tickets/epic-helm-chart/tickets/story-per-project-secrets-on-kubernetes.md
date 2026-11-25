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
tracker_status: backlog
---

# Per-project secrets on Kubernetes

## Description

The chart mounts per-project secrets (GitHub App key, PAT, embedder key) from Kubernetes Secrets as the secret files plotroom reads, and never renders a secret value into the ConfigMap.

## Requirements

Verify: A kind install with a Secret for an App key polls the test repository with the App, and helm template output contains no secret value.

## References

- parent — _bmad-output/initiative-orchestration-control-plane/tickets/epic-helm-chart/epic-helm-chart.md
