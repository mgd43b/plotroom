---
id: 3
type: story
title: "The stdio proxy works with real Claude Code and Codex clients"
parent: epic-server-and-ledger
covers: [R4, R8]
after: [2]
hitl: true
risk: high
tracker_id: "14"
remote: "https://github.com/mgd43b/plotroom/issues/14"
tracker_status: backlog
---

# The stdio proxy works with real Claude Code and Codex clients

## Description

Hardens plotroom connect --stdio: derives the HTTP envelope from message bodies in both protocol eras, handles concurrent calls and streamed progress, maps cancellation, starts the daemon with a clean environment when none runs, and reports the lock refusal. A person drives a real Claude Code session and a real Codex session through it; these are the spine's client spikes.

## Requirements

Verify: A real Claude Code session and a real Codex session each call snapshot() through the proxy with the daemon started on demand, and cancelling a slow call ends it on the server.

## References

- parent — _bmad-output/initiative-orchestration-control-plane/tickets/epic-server-and-ledger/epic-server-and-ledger.md

## Notes

- Check beyond its criteria: the proxy holds two concurrent calls, one of them a long wait, without blocking the other.
