---
title: "End-to-end suite over the epic's Done when"
type: feature
ticket: 6
created: '2026-11-25'
status: built
---

## Provenance

Retrospective plan reconstructed on 2026-11-25 for BMad 5.6 and [GitHub #81](https://github.com/mgd43b/plotroom/issues/81). The [shared Epic 5 plan](story-tracer-a-conflicting-claim-raises-an-overlap-item-plan.md) captured the original task and review record. This file records the as-built evidence. No independent plan baseline or separate historical approval was established.

## Intent

Exercise each of the five Epic 5 Done-when lines against an authenticated local daemon using deterministic repository facts.

## Scope and acceptance

Keep five named checks for PR/claim overlap, tree moves and suggested order, claim overlap timing, overlap and claim read parity, and worker/orchestrator ownership.

## Implementation evidence

The shared plan’s #81 task explicitly names the daemon and in-process fixture repository API. PR #150 adds all five scenarios in `tests/test_claims_e2e.py` and verifies the populated upgrade to migration `0013`.

## Verification

Five daemon acceptance tests and both exact-main Python jobs verified the fixture-backed implementation. `Repository.handle` serves the fixture through HTTPX mock transport; this evidence does not establish a run against the dedicated test repository. The canonical story’s criterion and recorded completion are retained.

- [Built in PR #150](https://github.com/mgd43b/plotroom/pull/150) at merge `3ecf20da2ba64a6e5e2e18f4e41b50db05b3fab0`.
- [Exact-main CI 45201121690](https://github.com/mgd43b/plotroom/actions/runs/45201121690), attempt 1, Python 3.14 and 3.15 passed.
