---
id: 11
type: story
title: "Release to PyPI and ghcr.io"
parent: epic-server-and-ledger
covers: [R10]
after: [10, 15]
hitl: true
risk: low
tracker_id: "25"
assignee: "mgd43b"
remote: "https://github.com/mgd43b/plotroom/issues/25"
tracker_status: backlog
---

# Release to PyPI and ghcr.io

## Description

release-please on Conventional Commits keeps a release PR; merging it after CI passes publishes wheels to PyPI through trusted publishing and the image to ghcr.io. The owner creates the PyPI project's trusted publisher and allows the workflow to push to ghcr.io.

## Requirements

Verify: A first release publishes plotroom to PyPI and the image to ghcr.io only after CI succeeds on the release commit, and uvx plotroom serve runs from the published wheel.

## References

- parent — _bmad-output/initiative-orchestration-control-plane/tickets/epic-server-and-ledger/epic-server-and-ledger.md

## Notes

- Hands on: the release workflow to epic 11's chart publishing (entry 11.5).
