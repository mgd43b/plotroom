---
id: 2
type: story
title: "Tracer: a merged PR on a test repository reaches the projection once"
parent: epic-one-inbox
covers: [R1, R2]
after: [1.4, 1.5, 1.6]
hitl: true
risk: high
tracker_id: "29"
remote: "https://github.com/mgd43b/plotroom/issues/29"
tracker_status: backlog
---

# Tracer: a merged PR on a test repository reaches the projection once

## Description

The thinnest path through the GitHub side: a credential provider with a fine-grained PAT (read from the environment of a plotroom serve started by hand, or from a mounted secret file named in config, which is how a daemon that plotroom connect --stdio starts on demand, with a clean environment, gets it), one configured repository per project, a poller built as one fetcher per resource (later entries add fetchers without editing shared code) with its own cursor, PR opened and merged as github.* events with versioned upstream_ids, core's projection of PRs and head SHAs, and a count of open PRs in snapshot(). The owner creates the test repository and one fine-grained PAT scoped to it with contents, pull requests, checks, actions, issues, deployments and workflows (read, plus write for test fixtures), and adds it as a CI secret. Hands on: the credential provider to entries 6, 10, 11 and 16; the poller and projection to entries 14, 3, 4, 15 and 5 and to epics 3, 4, 5 and 7.

## Requirements

Verify: Against the test repository, opening and merging a PR produces each github.* event once, and snapshot() shows the open-PR count rise and fall while the projection shows the PR merged at its head SHA; a daemon started on demand by the stdio proxy polls with the PAT from its secret file.

## References

- parent — _bmad-output/initiative-orchestration-control-plane/tickets/epic-one-inbox/epic-one-inbox.md
- architecture-orchestration-control-plane/architecture-orchestration-control-plane.md#ad-2
- architecture-orchestration-control-plane/architecture-orchestration-control-plane.md#ad-16

## Notes

- Check beyond its criteria: a revoked or expired PAT stops polling with a clear error and an unavailable project, and its cursor is intact when a new PAT is configured.
