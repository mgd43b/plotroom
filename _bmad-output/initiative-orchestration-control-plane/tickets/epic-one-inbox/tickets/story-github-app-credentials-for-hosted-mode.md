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
tracker_status: done
---

# GitHub App credentials for hosted mode

## Description

The credential provider gains a GitHub App installation token, minted from the App's key in a mounted secret and refreshed before expiry, used when hosted; the PAT stays the local default. The owner creates the GitHub App, installs it on the test repository and adds its key as a CI secret.

## Requirements

Verify: Under a fake Clock with a short token lifetime, a hosted instance configured with the App keeps polling the test repository across a refresh, and a scan of the project file and logs finds neither the key nor any token.

## References

- parent — _bmad-output/initiative-orchestration-control-plane/tickets/epic-one-inbox/epic-one-inbox.md
- architecture-orchestration-control-plane/architecture-orchestration-control-plane.md#ad-12

## Notes

- Hands on: the App credential to epic 11's Kubernetes secrets (entry 11.2).
- Decision, 2026-11-27, Matt in the Plotroom coordinator chat: “Accept the local run and close #39 (Recommended)”. The choice accepts the locally mounted App-key run for this story and leaves hosted runtime and CI-secret setup deferred.
- Live fixture evidence on `f43bf770150c8346766095b3e32fc0b2d0e6f43c`: a disposable Plotroom daemon loaded the App key from a mounted file and committed two real PR polls of the dedicated test repository. The run shortened the adapter-visible expiry and advanced a fake clock. The second poll used a distinct repository-scoped, read-only installation token with a later provider-reported expiry. Configuration, logs, and project data were scanned for credentials, with none found. [Public acceptance witness](https://github.com/mgd43b/plotroom/issues/39#issuecomment-7218523340)
- This was a local App-mode run. Hosted execution and CI-secret setup remain deferred, and #122 retains its separate Kubernetes Secret acceptance.
