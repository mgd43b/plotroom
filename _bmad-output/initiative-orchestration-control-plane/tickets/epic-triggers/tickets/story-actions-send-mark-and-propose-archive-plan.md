---
title: "Actions: send, mark and propose_archive"
type: feature
ticket: 6
created: '2026-11-23'
status: built
baseline_revision: '518642b7e89ca29eec8ce4f693f48f85eb966b02'
route: full
route_source: auto
risk: medium
review: quick
review_source: pinned
lenses_ran: [quick]
review_loop_iteration: 0
context: ['{project-root}/AGENTS.md']
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Dispatch the closed local trigger action set.

**Approach:** Use cause-derived keys for send, notify_orchestrator, mark, and propose_archive. Resolve worker channels from the project configuration, reuse Switchboard send, and update local lane state through the normal writer rules.

## Boundaries & Constraints

Use the local synthetic inputs in this build, with the committed GitHub projection and existing writer and action contracts. Preserve project isolation and existing rows. Scope is the six assigned stories. Hosting, live client setup, and native drain delivery are outside this implementation.

## I/O & Edge-Case Matrix

| Scenario | Expected behavior |
| --- | --- |
| Two archive proposals for the same lane | One state-backed archive item and no lane archival. |

</frozen-after-approval>

## Code Map

- `core/trigger_actions.py, core/lanes.py, app/runtime.py` — Validate action names and arguments. Use the normal lane transitions, and dispatch local effects in the action-completion transaction.
- `app/config.py, app/serve.py` — Use the project worker routing and existing relay adapter. Stage Claude sends for the outbox drain.
- `tests/test_trigger_actions.py` — Exercise synthetic Switchboard routing, cause-derived keys, lane transitions, archive deduplication, and invalid actions.

## Tasks & Acceptance

- [x] `core/trigger_actions.py, core/lanes.py, app/runtime.py` — Validate action names and arguments. Use the normal lane transitions, and dispatch local effects in the action-completion transaction.
- [x] `app/config.py, app/serve.py` — Use the project worker routing and existing relay adapter. Stage Claude sends for the outbox drain.
- [x] `tests/test_trigger_actions.py` — Exercise synthetic Switchboard routing, cause-derived keys, lane transitions, archive deduplication, and invalid actions.

Given a synthetic project daemon, when the relevant input is submitted through the writer or MCP surface, then the behavior in the edge-case matrix is observable in the durable project state.

## Implementation Notes

- Dispatched keyed Switchboard sends, writer-owned lane marks, and state-backed archive proposals through the existing runtime.

## Plan Change Log

## Review Triage Log

- Independent native review and follow-up. Preserved mixed route configuration by using an explicit Switchboard address for outbound delivery when it exists. Verified archive proposals deduplicate by lane and normal archival clears the item.

## Verification

The local trigger, recovery, action, transport, and migration suite passed all 50 tests on Python 3.14. Ruff, Pyright, and the core dependency check passed. Independent native review confirmed the corrected acceptance paths.

Run `uv run python -m pytest -q` and check that the targeted local acceptance tests pass, including the existing transport contract.
