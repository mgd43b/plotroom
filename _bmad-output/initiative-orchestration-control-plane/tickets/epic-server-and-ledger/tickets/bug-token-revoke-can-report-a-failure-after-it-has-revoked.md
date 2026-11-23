---
id: 23
type: bug
title: "Token revoke can report a failure after it has revoked"
parent: epic-server-and-ledger
covers: [R12]
after: [5]
refined: false
hitl: false
risk: medium
---

# Token revoke can report a failure after it has revoked

## Description

From an independent review of #134. plotroom token revoke removes the token from the instance token file first, then from the local tokens file. If the local cleanup fails, the command reports a failure although the revocation has taken effect. The fix keeps the instance revocation authoritative. It reports a failed local cleanup separately, saying the token is revoked, and lets a retry finish the cleanup. Malformed instance or local token files fail through the existing TokenError paths and the configuration exit, never a traceback. The missing regression tests land with it.

## Requirements

Verify: With the local tokens file unwritable, revoke leaves the token refused by the server, says only the local cleanup failed, and a retry once the file is writable removes the local entry. Malformed token files (bad JSON, the wrong shape, the wrong mode, not a regular file) give a TokenError message and the configuration exit. The regression tests pass in CI.

## References

- parent — _bmad-output/initiative-orchestration-control-plane/tickets/epic-server-and-ledger/epic-server-and-ledger.md
- found in — https://github.com/mgd43b/plotroom/pull/134
