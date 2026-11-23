---
id: 18
type: bug
title: "Tests can lose their daemon's port between picking and binding it"
parent: epic-server-and-ledger
covers: [R8, R11]
after: [2]
refined: false
hitl: false
risk: medium
tracker_id: "91"
remote: "https://github.com/mgd43b/plotroom/issues/91"
tracker_status: backlog
---

# Tests can lose their daemon's port between picking and binding it

## Description

Tests pick a free port and release it before the daemon binds it, so another process can take it in between; the daemon instead binds the port it is given or port 0 and publishes the port it bound (in its lock file or a ready line), and tests read it back (found by lane #14 in PR #51). A flaky test is a bug: fixed here, never rerun.

## Requirements

Verify: The daemon started with port 0 reports the port it bound and serves on it, and the test suite starts every daemon that way with no free-port helper left.

## References

- parent — _bmad-output/initiative-orchestration-control-plane/tickets/epic-server-and-ledger/epic-server-and-ledger.md
