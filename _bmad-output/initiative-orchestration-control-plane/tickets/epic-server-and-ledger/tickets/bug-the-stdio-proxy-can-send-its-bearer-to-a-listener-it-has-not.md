---
id: 21
type: bug
title: "The stdio proxy can send its bearer to a listener it has not authenticated"
parent: epic-server-and-ledger
covers: [R12]
after: [5]
refined: false
hitl: false
risk: high
---

# The stdio proxy can send its bearer to a listener it has not authenticated

## Description

Four related findings from an independent review of #134. In plotroom connect --stdio, the local token choice, the daemon start, the published-listener lookup and the request dispatch each resolve the instance separately. The proxy also forwards its bearer to whatever answers at the published address, and counts that listener's 401 as readiness. The fix resolves the instance once (config, data directory, lock owner and its published loopback endpoint) and binds token selection, start, lookup and dispatch to that one resolution. It authenticates the listener as that instance's daemon before it sends a bearer or counts a 401 as local readiness. It connects to the published loopback endpoint and keeps the supported configured Host semantics (Host localhost when the config says localhost). Each finding's missing regression test lands with it.

## Requirements

Verify: A listener at the published address that is not the resolved instance's daemon receives no bearer, and the proxy does not report it ready. Token selection, start, lookup and dispatch use one resolved instance, even when the config or port file changes between steps. A config with host localhost still connects with Host localhost. One regression test per finding passes in CI.

## References

- parent — _bmad-output/initiative-orchestration-control-plane/tickets/epic-server-and-ledger/epic-server-and-ledger.md
- found in — https://github.com/mgd43b/plotroom/pull/134
