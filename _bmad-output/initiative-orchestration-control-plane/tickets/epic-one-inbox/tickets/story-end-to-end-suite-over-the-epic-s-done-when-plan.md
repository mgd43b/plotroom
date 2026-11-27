---
title: 'Epic 2 acceptance runner'
type: feature
ticket: 13
created: '2026-11-27'
status: in-progress
baseline_revision: '481757f70cb489ffd0afc9bf35e041b67de80dfc'
route: full
route_source: auto
risk: low
review: thorough
review_source: pinned
lenses_ran: [blind-hunter, edge-case-hunter, verification-gap, intent-alignment]
review_loop_iteration: 0
context: [AGENTS.md]
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

Build one bounded Epic 2 acceptance entry point that runs the repeatable local-daemon and Compose witnesses available today, with a row for each of the nine epic Done-when clauses. Preserve the original live-fixture and CI credential requirements. Full acceptance must fail closed until the missing real witnesses can run.

## Boundaries & Constraints

Reuse existing tests, Compose, bridge, and scripted relay helpers. Use synthetic credentials and inputs exclusively. No live provider calls, owner key access, credential or workflow setup, GitHub fixture writes, deployments, hosted rollout, or publishing. Keep issue #46 and its canonical story open. Do not change production code.

| Scenario | Expected |
| --- | --- |
| Explicit synthetic run | Run the fixed local and Compose checks. Only their verified results may pass, with blocked real criteria visible and synthetic-only status clear. |
| Full run without real inputs | Nonzero and report the real blockers, even when all synthetic checks pass. |
| Test or Compose failure | Nonzero, visible failed environment, disposable resource cleanup. |
| Compose worker delivery | Replay one authenticated worker report twice and verify one durable event. A waiting MCP observer and the CLI observe the same new item from the pre-event cursor. Acknowledge the item. |

</frozen-after-approval>

## Code Map

- `deploy/epic_two_acceptance.py`: new runner. Run with `uv run --locked python -m deploy.epic_two_acceptance --image-tag <local-tag> --synthetic`. Use a generated Compose project name and existing bridge smoke; a missing image fails before starting a stack. Use explicit synthetic mode for a zero exit. Full mode runs the same safe available checks and exits nonzero for required real witnesses. Surface all nine clauses with per-environment scope, including strong synthetic evidence versus wiring or evidence still missing. No live adapter stub that can claim acceptance.
- Fixed pytest witnesses use `test_github.py::test_pull_lifecycle_and_credentials_preserve_committed_state`, `test_github_details.py::test_github_transitions_and_pagination_are_durable`, `test_switchboard.py::test_switchboard_redelivery_after_interrupted_ack`, `test_intake.py::test_worker_ingest_wakes_local_watch_and_ack_closes`, `test_github_app.py::test_app_daemon_keeps_polling_across_short_installation_tokens`, `test_work_items_github_tree.py::test_default_branch_tick_matches_checkout_and_preserves_last_good_view`, `test_github_details.py::test_github_issues_flow_through_the_runtime_tick`, `test_work_items_github_issues.py::test_work_items_match_the_synced_bmad_board`, `test_deploy_status.py::test_paginated_get_only_deployments_isolate_failures_and_deduplicate`, and `test_deploy_status.py::test_only_own_lane_worker_can_report_and_replay`. Prefix `tests/` for pytest. Epic Notes hold the spike answer, with no new runtime test for that finding.
- `deploy/bridge_smoke.py`: reuse disposable stack and synthetic token issuer. Add an authenticated Compose ingest and actual CLI watch witness, arrange a pre-event cursor from snapshot, use a separate `wait` progress callback as deterministic evidence that a daemon waiter is armed, and assert the CLI and MCP waiter see the same item. Confirm the duplicate tool result reuses the event and verify count with the existing read-only SQLite check, then ack. CLI child gets only the synthetic bearer and explicit loopback URL, bounded subprocess and cancellation cleanup. Extend shared bridge witness to assert the one committed event names the lane inferred from the configured sender. Preserve restart, ack-after-commit and outbound send. Avoid touching the shared workflow.
- `tests/test_intake.py`: reuse the real readiness gate from `test_watch.py` to arm the in-process daemon's CLI waiter before delivering a worker report, if needed for actual wake proof.
- `tests/test_epic_two_acceptance.py`: test the runner's synthetic versus incomplete/full exit behavior and failures without Docker/provider calls.

## Tasks & Acceptance

- [x] Run scoped synthetic pytest witnesses and real disposable Compose from one runner. Report each of the nine criteria and genuine blockers.
- [x] Extend shared Compose smoke with durable Claude replay, observed worker wake, and sender-lane checks.
- [x] Verify explicit synthetic exit success and full-mode nonzero, both Python paths and actual Compose; review independently.

## Implementation Notes

This plan remains in progress for full #46 acceptance. Build a local image from the checked-out
source and select its tag. The runner verifies the exact image ID Compose uses. The local daemon
test gates the actual CLI waiter. Compose gates an MCP waiter, checks that the CLI independently
reads the same item from a saved cursor, and acknowledges the item through MCP.

Remaining full inputs are the approved fixture GitHub run and PAT in CI, a hosted bridge and App-refresh run using the approved CI App credential path, a BMad-shaped GitHub Issues fixture on the same repository as the default-branch tree, a real recorded deployment and lane report in the suite, and live projected branch/PR/check/thread identity. The fixture currently has zero milestones and deployments. Existing separated acceptance runs and synthetic checks corroborate individual behavior.

## Plan Change Log

## Review Triage Log

| Lens | Verdict and disposition |
| --- | --- |
| Blind | Medium, patched. Compose confirms an armed MCP wake and a CLI cursor read. The local daemon test gates the actual CLI waiter. |
| Blind | Medium, patched. Compose uses the local image by immutable ID and verifies the running container ID. Build the tag from the same checked-out source before running. |
| Blind | Medium, patched. The runner checks the canonical Notes section for the recorded spike finding. |
| Blind | Medium, patched. Local and Compose now accept both a turn report and a session message, with durable lane assertions. |
| Blind | Medium, patched. The JUnit report must contain every exact selected case, including the intended parameter variants. |
| Edge | Medium, patched. The Compose waiter and CLI results are described according to their observed behavior. |
| Edge | Medium, patched. Exact JUnit identities replace the aggregate count test. |
| Verification | Medium, patched. The cursor-only Compose CLI evidence is explicit, and the local test proves the previously armed CLI wakes. |
| Verification | Medium, patched. Regression tests reject a missing exact witness even if an unrelated passing case keeps the total unchanged. |
| Intent | Medium, patched. The report distinguishes Compose CLI observation from an active MCP wake. |
| Intent | Low, patched. The synthetic mounted-PAT startup witness is included, and real credential-backed CI remains explicit. |
| Intent | Low, patched. The isolated Compose project disables unrelated watcher alerts while exercising the inbox. |

Follow-up inspection confirmed the cursor wording, exact test identity gate and immutable image pinning.

## Verification

- Locked Python 3.14 and 3.15 runner/unit and focused witness tests.
- Real local Compose with a uniquely tagged local image and per-run project. Verify resources cleaned.
- Full mode and synthetic mode exit and report checks.
- Ruff, type checking and whitespace checks as applicable.

Python 3.14 and 3.15 each passed 13 fixed local witnesses and eight runner unit cases.
Disposable Compose on the pinned base product image verified restart, relay commit-before-ack
and redelivery, durable lane inference, report deduplication, session-message ingest,
an armed MCP wake, CLI observation from the prior cursor, and item acknowledgment.
The runner verified the exact running image ID. Docker containers, networks, and volumes
were removed. Explicit synthetic mode exited 0 and default full mode exited 1 after
reporting six blocked real criteria. Ruff lint and formatting, Pyright, and whitespace
checks passed.
