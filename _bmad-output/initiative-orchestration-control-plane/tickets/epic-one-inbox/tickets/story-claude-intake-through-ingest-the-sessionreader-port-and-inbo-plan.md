---
title: Ingest Claude events and surface inbound messages
type: feature
ticket: 9
created: '2026-11-23'
status: built
baseline_revision: afe463bc58840dfff86021320b1f7b8fd737fe2d
route: full
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

Authenticate Claude intake against the project ledger and give the operator one actionable next item per inbound signal. Make a turn's known index and exact report digest durable aliases so a session reader and ingest can agree about its identity.

## Boundaries & Constraints

The worker's current lane is resolved inside the writer transaction. Provider events require their existing adapter path. The turn event, aliases, and next item commit together. Session file reads are covered by the subsequent reader story.

</frozen-after-approval>

## Code Map

Core owns intake and next-item creation. The MCP tool uses the authenticated HTTP context already used for lane commands. SQLite serializes alias writes with the originating event.

## Tasks & Acceptance

- [x] Add typed Claude intake and the SessionReader port.
- [x] Persist index and digest aliases, preserving the original event and item on replay.
- [x] Open an event-backed next item for each newly committed inbound message.

Given a worker session with an active lane, when it sends a turn for another lane, then the single durable event belongs to the worker's lane, a provider event is forbidden, and a waiting operator sees an item that ack closes.

## Implementation Notes

Authenticated transport checks exercised both provider-kind refusal and worker session ownership, then verified a local watch wake and item closure. Distinct indexed turns can share a report digest.

## Plan Change Log

## Review Triage Log

The independent native Codex review inspected the combined batch.

- P1, verified and corrected. Refuse a bound session ID from another worker before looking up its aliases.

## Verification

- uv run python -m pytest tests/test_intake.py tests/test_switchboard.py tests/test_transport_contract.py tests/test_migrations.py
