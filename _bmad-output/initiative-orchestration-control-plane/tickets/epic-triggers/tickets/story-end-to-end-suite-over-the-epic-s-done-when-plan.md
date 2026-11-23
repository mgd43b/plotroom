---
title: 'Epic 3 acceptance suite'
type: feature
ticket: 10
created: '2026-11-23'
status: in-review
baseline_revision: '3ecf20da2ba64a6e5e2e18f4e41b50db05b3fab0'
route: full
route_source: auto
risk: low
review: thorough
review_source: pinned
lenses_ran: [native]
review_loop_iteration: 0
context: []
---

<frozen-after-approval reason="human-owned intent">

## Intent

Exercise every Epic 3 Done-when line against a real local daemon with deterministic providers.

## Boundaries & Constraints

Use deterministic local fixtures and committed typed facts. External actions, hosted work, and real deployment and drain acceptance stay outside this build.

</frozen-after-approval>

## Code Map

- `tests/test_epic3_e2e.py` — Exercise an all_of commit/restart, scripted Switchboard and drain action delivery, refusals, and worker run plus same-SHA image readiness. Use local fixtures and both MCP transports.
- `tests/test_transport_contract.py` — Include the added typed worker commands in the transport and role contract.

## Tasks & Acceptance

- [x] `tests/test_epic3_e2e.py` — Exercise an all_of commit/restart, scripted Switchboard and drain action delivery, refusals, and worker run plus same-SHA image readiness. Use local fixtures and both MCP transports.
- [x] `tests/test_transport_contract.py` — Include the added typed worker commands in the transport and role contract.

Given local fixture adapters and configured tokens, when all four Done-when paths run through the MCP surface, then the daemon produces the expected durable trigger states and keyed deliveries.

## Implementation Notes

- Each of four named cases runs through HTTP and stdio. Tests poll an in-process fixture repository through the GitHub read adapters and use a scripted Switchboard process and the existing drain stand-in.

## Plan Change Log

## Review Triage Log

## Design Notes

The live inputs use deterministic local fixtures. Reuse the scripted Switchboard child and DrainStandIn.
The reports are `run_report(lane, run_id, state, request_id)` using running and succeeded and
`deploy_report(lane, environment, sha, state, request_id)` using full lowercase SHA and success.
The leaves are `{"run_finished": {"run_id": "job"}}` and
`{"main_green_with_images": {"sha": "<full lowercase SHA>"}}`. Main uses the existing complete
MAIN snapshot and configured image-job names from GitHubConfig. The latest completed workflow's
head must be the current projected main tip. Both transports can use the helpers in
`tests/test_transport_contract.py`. Use local test tokens only. The suite verifies the fixture drain
without contacting a Claude session.

## Verification

Run `uv run python -m pytest -q tests/test_epic3_e2e.py tests/test_transport_contract.py`.
