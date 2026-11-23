---
id: 19
type: bug
title: "A SIGTERM'd daemon may skip its teardown"
parent: epic-server-and-ledger
covers: [R1, R8]
after: [2]
refined: false
hitl: false
risk: medium
tracker_id: "129"
remote: "https://github.com/mgd43b/plotroom/issues/129"
tracker_status: done
status: built
---

# A SIGTERM'd daemon may skip its teardown

## Description

From uvicorn's source, a daemon stopped with SIGTERM appears to skip Daemon.run's teardown, including _close_projects, which would leave project files without a clean close (found by the #91 lane; inferred, untested). The first step is a test that sends SIGTERM to a running daemon and checks that teardown ran; if it did not, the fix makes SIGTERM shut down through the same teardown as a normal stop.

## Requirements

Verify: A test that sends SIGTERM to a running daemon with an open project shows _close_projects ran and the project file closed cleanly, and passes in CI.

## References

- parent — _bmad-output/initiative-orchestration-control-plane/tickets/epic-server-and-ledger/epic-server-and-ledger.md

## Notes

- Built: https://github.com/mgd43b/plotroom/pull/135
