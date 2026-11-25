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

Verify: A CI run on a PR and on main produces one event per check state change, a rerun on the same head is a new fact, and the projection reports main's latest completed verdict and its image-build checks. Using the authorized dedicated repository and its identified CI and image-build workflow, re-verify #34's review-thread projection and #56's green-check, resolved-thread, and closed-issue triggers. Verify #57's lane report and same-commit main/image readiness, #63's test-PR gate verdict and as-of, and #65's flake-candidate outcomes against suitable recorded run history. Retain the deterministic negative cases and never rerun a job to produce evidence. Record a public-safe result or a specific missing live input for each issue.

## References

- parent — _bmad-output/initiative-orchestration-control-plane/tickets/epic-one-inbox/epic-one-inbox.md
- Built: https://github.com/mgd43b/plotroom/pull/147
