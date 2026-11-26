---
id: 3
type: story
title: "First provider usage adapter (OpenRouter)"
parent: epic-guardrails
covers: [R1, R2]
after: [1, 1.10, 2.2]
hitl: true
risk: high
tracker_id: "102"
remote: "https://github.com/mgd43b/plotroom/issues/102"
tracker_status: backlog
---

# First provider usage adapter (OpenRouter)

## Description

Add the first real UsageMeter adapter for OpenRouter. For each run, read the dedicated inference key from a mounted file using the existing secret-file reader (C2). The key is scoped to one run with a provider-enforced USD cap and no reset. Use that same key to read cumulative USD usage from `GET /api/v1/key` (`data.usage`). Preserve the existing UsageMeter port and monotonic budget treatment. The trial and its spend claim exclude bring-your-own-key (BYOK) usage. Direct Anthropic Console support remains later work.

## Requirements

Verify the adapter's read against the same dedicated key's real cumulative USD usage after allowing for reporting delay, and use deterministic fixtures to verify cumulative readings and missing or delayed data. Reconcile the existing authorized one-call trial's $0.0000033 response cost, generation cost and key-usage delta. A scan finds no credential in records or logs.

## References

- parent — _bmad-output/initiative-orchestration-control-plane/tickets/epic-guardrails/epic-guardrails.md
- [OpenRouter current-key API](https://openrouter.ai/docs/api/api-reference/api-keys/get-current-key)

## Notes

- Paid run: the capped test run needs the coordinator's go-ahead when this is built, with a provider-enforced cap, a trial first and a projection from the trial's actual spend.
- Check beyond its criteria: a reporting delay or a gap in the provider's data never lowers a budget's recorded spend.
- Superseded decision, 2026-11-22: Anthropic was selected first because the lanes are Claude sessions. Direct Anthropic Console support is deferred behind the same port.
- Decision, 2026-11-26, Matt’s instruction in the Plotroom coordinator chat, as quoted in the handoff: “let’s make OpenRouter the first provider for #102. update the ticket and have a worker build it. keep direct Anthropic Console support for later.”
- Trial evidence, 2026-11-26: the observed one-call OpenRouter charge was $0.0000033 in the response and generation record and as the increase in the same key’s cumulative usage. The adapter verification remains part of this story.
