---
title: Send committed Switchboard actions
type: feature
ticket: 17
created: '2026-11-23'
status: in-review
baseline_revision: af28da0ede39ff4730d2b53bf74abac9e6717bbe
route: oneshot
route_source: auto
risk: medium
review: thorough
review_source: pinned
lenses_ran: []
review_loop_iteration: 0
context: []
---

<frozen-after-approval reason="human-owned intent">

## Intent

**Problem:** A committed local action cannot deliver a message to a Switchboard peer.

**Approach:** Route Switchboard send actions through the registered participant and carry the persisted action key in the message and relay request.

</frozen-after-approval>

## Implementation Notes

Reuse ProjectRuntime's attempt transaction. The scripted peer accepts a send before interrupting the response. On restart it records a second request with the same key and lets the action finish.

## Plan Change Log

## Review Triage Log

## Verification

- uv run python -m pytest tests/test_switchboard.py tests/test_actions.py
