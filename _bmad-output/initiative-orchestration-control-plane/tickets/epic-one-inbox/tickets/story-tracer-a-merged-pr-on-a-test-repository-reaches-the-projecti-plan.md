---
title: Project read-only GitHub pull requests
type: feature
ticket: 2
created: '2026-11-23'
status: built
baseline_revision: 10881fe4d5ae596bc10fc1e2dc43aeeb9a76a126
route: full
route_source: auto
risk: high
review: thorough
review_source: pinned
lenses_ran: []
review_loop_iteration: 0
context: []
---

<frozen-after-approval reason="human-owned intent">

## Intent

Poll one configured repository for opened and merged pull requests. Store typed facts and the resulting PR projection and resource cursor in one transaction. Surface the open count in snapshot.

## Boundaries & Constraints

Use a local HTTP fixture and fixture credentials for worker verification. The repository owner handles the dedicated live repository, fine-grained PAT, and CI setup. The poller issues read-only requests. Credentials stay out of events and logs.

</frozen-after-approval>

## Code Map

The local daemon runs project input tasks. The GitHub adapter owns HTTP and credential loading, and core owns event creation and projection. Snapshot reads committed project state.

## Tasks & Acceptance

- [x] Read the project's PAT from the environment or the configured mounted file.
- [x] Parse GitHub pull request snapshots and project opened and merged facts atomically.
- [x] Verify lifecycle, invalid credentials, and the on-demand daemon against an HTTP fixture.

## Implementation Notes

The loopback HTTP fixture verified environment credentials, missing and rejected credentials, and an on-demand daemon with a mounted token file. The dedicated repository's live fixture acceptance passed, including on-demand startup with a scoped PAT read from its configured file. The live deployment sample remains pending because the fixture contained zero deployment records. [Acceptance run](https://github.com/mgd43b/plotroom/actions/runs/45845942573)

## Plan Change Log

## Review Triage Log

The independent native Codex review inspected the combined batch.

- P2, verified and corrected. Retain the PR head repository so fork PRs count without attaching to local lanes.

## Verification

- uv run python -m pytest tests/test_github.py tests/test_migrations.py tests/test_transport_contract.py
