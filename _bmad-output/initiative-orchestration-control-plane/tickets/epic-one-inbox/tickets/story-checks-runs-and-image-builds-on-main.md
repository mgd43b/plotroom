---
id: 3
type: story
title: "Checks, runs and image builds on main"
parent: epic-one-inbox
covers: [R1]
after: [14]
hitl: true
risk: medium
tracker_id: "33"
remote: "https://github.com/mgd43b/plotroom/issues/33"
tracker_status: backlog
---

# Checks, runs and image builds on main

## Description

A fetcher adds checks per head SHA and workflow runs on the default branch, including the configured image-build checks, each state change a new fact (<check_run_id>@<status>/<conclusion>), with each run's job conclusions, into the projection, which reports the latest run per check name. The owner adds a CI workflow with an image-build job to the test repository. Hands on: main's verdict and image checks to epics 3 and 4.

## Requirements

Verify: A CI run on a PR and on main produces one event per check state change, a rerun on the same head is a new fact, and the projection reports main's latest completed verdict and its image-build checks.

## References

- parent — _bmad-output/initiative-orchestration-control-plane/tickets/epic-one-inbox/epic-one-inbox.md
- Built: https://github.com/mgd43b/plotroom/pull/147
