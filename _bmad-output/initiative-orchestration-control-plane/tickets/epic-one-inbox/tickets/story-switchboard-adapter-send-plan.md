---
title: Send committed Switchboard actions
type: feature
ticket: 17
created: '2026-11-23'
status: built
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

The scripted relay accepted a send before its response was interrupted. The restarted action used the same provider idempotency key and message key.

## Plan Change Log

## Review Triage Log

The independent native Codex review inspected the combined batch.

- P3, verified and corrected. Publish complete send markers before the test can tear down the child process.

## Verification

- uv run python -m pytest tests/test_switchboard.py tests/test_actions.py
