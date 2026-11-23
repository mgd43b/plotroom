---
id: 22
type: bug
title: "An https bearer connection ignores configured CA trust"
parent: epic-server-and-ledger
covers: [R12]
after: [5]
refined: false
hitl: false
risk: medium
---

# An https bearer connection ignores configured CA trust

## Description

From an independent review of #134. To keep environment proxies away from a token, a credentialed proxy turns off httpx's environment handling. That also drops SSL_CERT_FILE and SSL_CERT_DIR, so an explicit https endpoint signed by a private CA cannot be reached. The fix adds configured CA trust for explicit https endpoints (its setting is named in the plan) and keeps environment proxy discovery off for credentialed connections. The missing regression tests land with it.

## Requirements

Verify: A credentialed connect to an https test server signed by a test CA succeeds with that CA configured, and fails with a clear error without it. A proxy set in the environment is never used for a credentialed connection. The regression tests pass in CI.

## References

- parent — _bmad-output/initiative-orchestration-control-plane/tickets/epic-server-and-ledger/epic-server-and-ledger.md
- found in — https://github.com/mgd43b/plotroom/pull/134
