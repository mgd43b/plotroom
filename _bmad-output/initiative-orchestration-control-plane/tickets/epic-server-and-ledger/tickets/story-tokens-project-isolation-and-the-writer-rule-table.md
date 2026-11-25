---
id: 5
type: story
title: "Tokens, project isolation and the writer-rule table"
parent: epic-server-and-ledger
covers: [R1, R7, R12]
after: [3, 4]
hitl: true
risk: medium
tracker_id: "16"
remote: "https://github.com/mgd43b/plotroom/issues/16"
tracker_status: done
status: built
---

# Tokens, project isolation and the writer-rule table

## Description

Declares the token roles (orchestrator, clerk, worker:<name>, drain, bridge) and system; plotroom token issue and revoke write hashed tokens straight to the instance token file under a file lock (reloaded without restart) and, locally, the 0600 tokens file the proxy reads; HTTP middleware maps a token to (project, role) and checks the Host header; core's writer-rule table declares every command and system write by role and denies anything undeclared; the stdio proxy's readiness probe counts a 401 as a server answering; a person confirms how a real Claude Code session and a real Codex session handle a 401 with no WWW-Authenticate (the client spike entry 3 could not run before tokens existed); the project config gains its drain_session key. Later epics append their rows. Hands on: the drain and bridge roles to epics 2 and 3.

## Requirements

Verify: With two projects and a fixture orchestrator-only command, an orchestrator token runs it, a worker token is refused it, project A's token cannot reach project B, a revoked token gets 401 and an unknown Host gets 421; with /mcp requiring a token, plotroom connect --stdio still finds the running daemon ready; real clients' handling of a bare 401 is recorded in the epic's Notes.

## References

- parent — _bmad-output/initiative-orchestration-control-plane/tickets/epic-server-and-ledger/epic-server-and-ledger.md

## Notes

- Hands on from #15 (PRs #50, #92): event kinds and commands register in core/catalog.py build_catalog(); an event's source must equal its kind's prefix; a duplicate returns the stored event whatever its fields, only after role, kind and source pass; migration 0002 carries CHECK(seq > 0) and CHECK(json_valid(fields)), and the next revision's parent is 0002; Ledger.execute checks no role yet. This story adds command authorization to Ledger.execute.
- Built: https://github.com/mgd43b/plotroom/pull/134
- The real-client bare-401 results are recorded in the Server and ledger epic Notes.
- An agent performed the independent real-client bare-401 observations in the Server and ledger epic Notes. [Merged evidence PR #181](https://github.com/mgd43b/plotroom/pull/181). Matt will decide whether to accept this evidence or repeat the check by hand.
