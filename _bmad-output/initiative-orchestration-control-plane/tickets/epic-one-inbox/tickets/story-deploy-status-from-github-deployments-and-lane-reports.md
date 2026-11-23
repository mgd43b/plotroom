---
id: 11
type: story
title: "Deploy status from GitHub Deployments and lane reports"
parent: epic-one-inbox
covers: [R9]
after: [2, 1.5, 1.8]
hitl: false
risk: low
tracker_id: "40"
remote: "https://github.com/mgd43b/plotroom/issues/40"
tracker_status: backlog
---

# Deploy status from GitHub Deployments and lane reports

## Description

A DeployStatus adapter reads the repository's deployments and their statuses through entry 2's credential provider, and a deploy_report command lets a deploy lane's worker token report environment, SHA and state for its own lane; its handler appends deploy.* as system. Both land as typed deploy events.

## Requirements

Verify: A deployment the test creates on the test repository and a lane's deploy_report each appear once with environment, SHA and state; the adapter's client is asserted to issue only GET requests; a deploy_report from another lane's token is refused.

## References

- parent — _bmad-output/initiative-orchestration-control-plane/tickets/epic-one-inbox/epic-one-inbox.md
