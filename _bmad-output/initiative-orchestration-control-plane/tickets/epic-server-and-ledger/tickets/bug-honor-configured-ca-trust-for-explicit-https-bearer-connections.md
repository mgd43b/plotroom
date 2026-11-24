---
id: 22
type: bug
title: "Honor configured CA trust for explicit HTTPS bearer connections"
parent: epic-server-and-ledger
covers: [R12]
after: []
refined: false
hitl: false
risk: medium
tracker_id: "139"
remote: "https://github.com/mgd43b/plotroom/issues/139"
tracker_status: done
---

# Honor configured CA trust for explicit HTTPS bearer connections

## Description

An explicit HTTPS connection that uses a bearer honors configured CA trust and uses default certificate validation when no CA is configured. Environment proxy discovery stays disabled. PR #134 supplies the implemented transport contract for this follow-up.

## Requirements

Verify: A fixture HTTPS server signed by a test CA accepts a bearer connection when that CA is configured. Untrusted certificates and hostname mismatches fail with a clear error. A proxy configured in the environment receives no connections from the credentialed client.

## References

- parent — _bmad-output/initiative-orchestration-control-plane/tickets/epic-server-and-ledger/epic-server-and-ledger.md
- found in — https://github.com/mgd43b/plotroom/pull/134
