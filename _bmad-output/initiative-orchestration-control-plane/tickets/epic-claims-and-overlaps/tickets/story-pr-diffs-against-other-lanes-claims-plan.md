---
title: "PR diffs against other lanes' claims"
type: feature
ticket: 2
created: '2026-11-25'
status: built
---

## Provenance

Retrospective plan reconstructed on 2026-11-25 for BMad 5.2 and [GitHub #77](https://github.com/mgd43b/plotroom/issues/77). The [shared Epic 5 plan](story-tracer-a-conflicting-claim-raises-an-overlap-item-plan.md) captured the original task and review record. This file records the as-built evidence. No independent plan baseline or separate historical approval was established.

## Intent

Reconcile confirmed current-head file facts against claims in the writer transaction and surface one state-backed item per PR and claiming lane. Exclude a lane’s own claims and check unassigned and fork PRs against every applicable claim.

## Scope and acceptance

Use the existing projection and writer boundaries. Claims made after file facts still trigger reconciliation. A replacement head, claim release or closed PR clears the resolved item; a read failure must respect confirmed-file identity.

## Implementation evidence

The original consolidated plan’s #77 task and Code Map specify this flow. PR #150 implements it in `core/pull_overlaps.py`, `core/github.py` and the projection and store ports. Commits `20169a7`, `74ec2c0` and `11bdf4dc` carry the implementation and follow-through.

## Verification

`tests/test_claims_e2e.py::test_pr_diff_raises_overlap_before_review` and `tests/test_claims.py::test_stale_resources_and_binding_changes_reconcile_all_open_prs` cover the authenticated board, file facts, claim and PR lifecycle. The later #152 file-binding correction is recorded separately.

- [Built in PR #150](https://github.com/mgd43b/plotroom/pull/150) at merge `3ecf20da2ba64a6e5e2e18f4e41b50db05b3fab0`.
- [Exact-main CI 45201121690](https://github.com/mgd43b/plotroom/actions/runs/45201121690), attempt 1, Python 3.14 and 3.15 passed.
