---
id: 1
type: story
title: "Tracer: helm install runs plotroom as one replica"
parent: epic-helm-chart
covers: [R1]
after: [1.10]
hitl: false
risk: medium
tracker_id: "121"
remote: "https://github.com/mgd43b/plotroom/issues/121"
tracker_status: backlog
status: built
---

# Tracer: helm install runs plotroom as one replica

## Description

The thinnest path: a chart in deploy/helm/ with one replica, the Recreate strategy, a ReadWriteOnce block volume holding the data directory (and the instance token file, AD-3), a Service, a ConfigMap for the TOML config, and a one-off Job that runs plotroom token issue against the volume; helm lint and helm template run in CI, and CI installs the chart on a kind cluster and waits for /readyz.

## Requirements

Verify: In CI the chart lints, renders, installs on kind with one pod that passes /readyz, a token issued by the Job gets snapshot() over HTTP, and deleting the pod brings a new one up only after the old one ends, with a recorded event still there.

## References

- parent — _bmad-output/initiative-orchestration-control-plane/tickets/epic-helm-chart/epic-helm-chart.md

## Notes

- Built: https://github.com/mgd43b/plotroom/pull/185
