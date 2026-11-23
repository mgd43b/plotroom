---
id: 3
type: story
title: "First provider usage adapter (Anthropic)"
parent: epic-guardrails
covers: [R1, R2]
after: [1, 1.10, 2.2]
hitl: true
risk: high
tracker_id: "102"
remote: "https://github.com/mgd43b/plotroom/issues/102"
tracker_status: backlog
---

# First provider usage adapter (Anthropic)

## Description

A UsageMeter adapter for Anthropic's usage and cost reporting, read with an admin credential from a mounted secret through entry 2.2's secret-file reader (never stored, C2), mapped to cumulative spend per run by the key scope or tag the run registered; the endpoint and fields are confirmed against Anthropic's current documentation at the plan checkpoint. The owner creates the read-only admin credential and makes a small tagged test run under a provider-enforced spend limit.

## Requirements

Verify: Against Anthropic's real reporting for the small tagged test run, the adapter reports cumulative spend that matches the console within the reporting delay, and a scan finds the credential in no record or log.

## References

- parent — _bmad-output/initiative-orchestration-control-plane/tickets/epic-guardrails/epic-guardrails.md

## Notes

- Paid run: the capped test run needs the coordinator's go-ahead when this is built, with a provider-enforced cap, a trial first and a projection from the trial's actual spend.
- Check beyond its criteria: a reporting delay or a gap in the provider's data never lowers a budget's recorded spend.
- Decision: 2026-11-22, Anthropic is the first provider (the lanes are Claude sessions); others are later adapters behind the same port.
