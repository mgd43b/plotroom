---
id: 6
type: story
title: "GitHub App credentials for hosted mode"
parent: epic-one-inbox
covers: [R2]
after: [2, 1.10]
hitl: true
risk: medium
tracker_id: "39"
remote: "https://github.com/mgd43b/plotroom/issues/39"
tracker_status: backlog
---

# GitHub App credentials for hosted mode

## Description

The credential provider gains a GitHub App installation token, minted from the App's key in a mounted secret and refreshed before expiry, used when hosted; the PAT stays the local default. The owner creates the GitHub App, installs it on the test repository and adds its key as a CI secret.

## Requirements

Verify: Under a fake Clock with a short token lifetime, a hosted instance configured with the App keeps polling the test repository across a refresh, and a scan of the project file and logs finds neither the key nor any token.

## References

- parent — _bmad-output/initiative-orchestration-control-plane/tickets/epic-one-inbox/epic-one-inbox.md
- architecture-orchestration-control-plane/architecture-orchestration-control-plane.md#ad-12
