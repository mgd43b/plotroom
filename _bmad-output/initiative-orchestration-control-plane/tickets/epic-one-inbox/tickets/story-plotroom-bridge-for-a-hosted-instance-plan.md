---
title: Bridge a local Switchboard participant over HTTP
type: feature
ticket: 8
created: '2026-11-24'
status: in-review
baseline_revision: '642e286c6dd225f90070c7a52c7137296dc502b8'
route: full
route_source: auto
risk: high
review: thorough
---

## Intent

Add the bridge CLI and scoped writer operations. Acknowledge each delivery only after the
corresponding event commits. Lease outbound Switchboard actions until the relay accepts them
with their stable action key.

## Acceptance and verification

- Actual Compose smoke uses a local scripted relay, a bridge token, and both MCP eras.
- Killing the bridge after commit and before ack leaves one stored event after redelivery.
- A trigger produces a leased send and the relay accepts it once.
- A current lease can complete, a stale lease cannot, and a retry retains the action key.

## Implementation notes

The bridge uses the existing action row and writer. There is no new schema revision. The
Compose smoke passed with an actual killed bridge, redelivery, and a completed outbound send.

## Independent review dispositions

Keep HTTP streams open through idle waits and wait for actionable work before leasing. Validate the configured board and enforce the action retry limit. Refuse lane-pinned budget stops over the bridge and raise a failed action for operator attention. True remote recipient fencing is a future candidate.
