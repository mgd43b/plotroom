---
id: 5
type: story
title: "Tokens, project isolation and the writer-rule table"
parent: epic-server-and-ledger
covers: [R1, R7, R12]
after: [3, 4]
hitl: false
risk: medium
tracker_id: "16"
remote: "https://github.com/mgd43b/plotroom/issues/16"
tracker_status: backlog
---

# Tokens, project isolation and the writer-rule table

## Description

Declares the token roles (orchestrator, clerk, worker:<name>, drain, bridge) and system; plotroom token issue and revoke write hashed tokens straight to the instance token file under a file lock (reloaded without restart) and, locally, the 0600 tokens file the proxy reads; HTTP middleware maps a token to (project, role) and checks the Host header; core's writer-rule table declares every command and system write by role and denies anything undeclared; the project config gains its drain_session key. Later epics append their rows. Hands on: the drain and bridge roles to epics 2 and 3.

## Requirements

Verify: With two projects and a fixture orchestrator-only command, an orchestrator token runs it, a worker token is refused it, project A's token cannot reach project B, a revoked token gets 401 and an unknown Host gets 421.

## References

- parent — _bmad-output/initiative-orchestration-control-plane/tickets/epic-server-and-ledger/epic-server-and-ledger.md
