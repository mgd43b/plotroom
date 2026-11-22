---
id: 15
type: story
title: "Transport contract test over every tool"
parent: epic-server-and-ledger
covers: [R4, R11]
after: [3, 5, 8, 9, 13]
hitl: true
risk: medium
tracker_id: "24"
remote: "https://github.com/mgd43b/plotroom/issues/24"
tracker_status: backlog
---

# Transport contract test over every tool

## Description

The contract test calls every tool from entries 2 to 13 through the stdio proxy and HTTP in both protocol eras, and covers a 401 with no WWW-Authenticate; it runs in CI. A person confirms how a real Claude Code session and a real Codex session handle that 401.

## Requirements

Verify: The contract test passes in CI for every registered tool in both transports and both eras, and fails if a tool is missing from either.

## References

- parent — _bmad-output/initiative-orchestration-control-plane/tickets/epic-server-and-ledger/epic-server-and-ledger.md
