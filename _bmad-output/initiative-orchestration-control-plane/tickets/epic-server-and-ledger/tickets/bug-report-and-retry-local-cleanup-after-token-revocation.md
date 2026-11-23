---
id: 23
type: bug
title: "Report and retry local cleanup after token revocation"
parent: epic-server-and-ledger
covers: [R12]
after: []
refined: false
hitl: false
risk: medium
tracker_id: "140"
remote: "https://github.com/mgd43b/plotroom/issues/140"
tracker_status: done
status: built
---

# Report and retry local cleanup after token revocation

## Description

The instance record is authoritative once token revocation commits. The CLI needs to report the successful revocation and any local cleanup failure separately and permit a retry of local cleanup. Invalid token files must fail closed through the CLI and HTTP error handlers.

## Requirements

Verify: When local cleanup fails, the server continues to refuse the revoked token, and the CLI reports that revocation succeeded and cleanup failed. A retry after the underlying problem is removed cleans up the local entry. Invalid, unsafe, or excessively nested token files produce controlled configuration errors in the CLI and closed authorization in HTTP without exposing token data.

## References

- parent — _bmad-output/initiative-orchestration-control-plane/tickets/epic-server-and-ledger/epic-server-and-ledger.md
- found in — https://github.com/mgd43b/plotroom/pull/134

## Notes

- Built: https://github.com/mgd43b/plotroom/pull/143
