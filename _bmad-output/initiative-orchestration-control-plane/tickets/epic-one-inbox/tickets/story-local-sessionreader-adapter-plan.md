---
title: Read local Claude Desktop session signals
type: feature
ticket: 18
created: '2026-11-23'
status: built
baseline_revision: de1ffb507681aa2d9429c6d1dfc21a3add9cdeee
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

Read configured local Claude Desktop sessions through the SessionReader port and route completed turns and inbound peer messages through the same transaction as authenticated ingest.

## Boundaries & Constraints

Use synthetic session files in automation. Read the three documented sources through explicit roots and accept known session IDs only. Resolve the matching current worker lane when recording. Never open key files or connect to session sockets. The owner checks a real desktop session.

</frozen-after-approval>

## Code Map

The Epic 2 spike links the registry session ID, Desktop session metadata, and transcript basename. The transcript user row's turn index is the key. Core intake persists the alternate digest and index identities.

## Tasks & Acceptance

- [x] Read and normalize joined session file data and handle missing or unrecognized records.
- [x] Wire the reader into local project startup through explicit worker/session config.
- [x] Verify reader and ingest in both arrival orders, with and without the turn index.

## Implementation Notes

Synthetic fixtures exercise both arrival orders, both index forms, and a repeated transcript read. The owner’s real Desktop observation remains open.

## Plan Change Log

## Review Triage Log

The independent native Codex review inspected the combined batch.

- P2, verified and corrected. Compare the registry status and update timestamp before and after scanning the latest turn.

## Verification

- uv run python -m pytest tests/test_session_reader.py tests/test_intake.py tests/test_config.py
