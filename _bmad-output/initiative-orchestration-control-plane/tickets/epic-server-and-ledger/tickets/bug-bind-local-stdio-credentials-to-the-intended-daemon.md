---
id: 21
type: bug
title: "Bind local stdio credentials to the intended daemon"
parent: epic-server-and-ledger
covers: [R12]
after: []
refined: false
hitl: false
risk: high
tracker_id: "138"
remote: "https://github.com/mgd43b/plotroom/issues/138"
tracker_status: backlog
---

# Bind local stdio credentials to the intended daemon

## Description

Local `plotroom connect --stdio` must use one canonical daemon instance for credential selection, startup, listener discovery, and request dispatch. The proxy must establish that it is connected to that daemon before sending a credential or MCP payload. PR #134 supplies the implemented token and transport contract for this follow-up. The remaining real-client observations on #16 are tracked there.

## Requirements

Verify: With controlled fixtures, confirm that a configuration or path change cannot split credential selection and listener discovery across instances. Confirm that an unverified connection carries zero credentials and MCP payloads. Accept a local 401 as readiness only after verifying the responding daemon's identity. Confirm that supported loopback aliases use the verified published endpoint and preserve an allowed Host header.

## References

- parent — _bmad-output/initiative-orchestration-control-plane/tickets/epic-server-and-ledger/epic-server-and-ledger.md
- found in — https://github.com/mgd43b/plotroom/pull/134

## Notes

- High risk because a credential or request may reach the wrong daemon. Also verify that the proxy reestablishes daemon identity after a daemon restart before resuming authenticated requests.
