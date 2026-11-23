---
id: 5
type: story
title: "Publish the chart"
parent: epic-helm-chart
covers: [R1]
after: [1, 1.11]
hitl: true
risk: low
tracker_id: "125"
remote: "https://github.com/mgd43b/plotroom/issues/125"
tracker_status: backlog
---

# Publish the chart

## Description

The release workflow publishes the chart as an OCI artifact to ghcr.io next to the image, with the chart's maintainer contact set to the owner's public identity; the owner allows the workflow to push it and makes the first-pushed chart package public.

## Requirements

Verify: A release publishes the chart to ghcr.io, and helm install from that OCI reference on kind, with no registry credentials, passes /readyz.

## References

- parent — _bmad-output/initiative-orchestration-control-plane/tickets/epic-helm-chart/epic-helm-chart.md

## Notes

- Making the chart package public is a publishing step that stays with mgd43b when its time comes.
