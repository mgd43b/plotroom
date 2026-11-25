---
title: "overlaps() and claims in snapshot()"
type: feature
ticket: 4
created: '2026-11-25'
status: built
---

## Provenance

Retrospective plan reconstructed on 2026-11-25 for BMad 5.4 and [GitHub #79](https://github.com/mgd43b/plotroom/issues/79). The [shared Epic 5 plan](story-tracer-a-conflicting-claim-raises-an-overlap-item-plan.md) captured the original task and review record. This file records the as-built evidence. No independent plan baseline or separate historical approval was established.

## Intent

Expose filtered PR and lane overlap reads, visible claim rows and reasons, and visibility-scoped snapshot counts. Use the same state-backed conflicts as next().

## Scope and acceptance

Apply the established lane and next-item visibility before paging. Validate that overlap queries select one target. Preserve existing transport and item-detail behavior when a response reaches the output budget.

## Implementation evidence

The shared plan’s #79 task and Implementation Notes distinguish claim visibility from overlap item routing. PR #150 adds the MCP surface, ledger reads and snapshot counts in `adapters/mcp/server.py`, `core/ledger.py` and `core/snapshot.py`.

## Verification

`tests/test_claims_e2e.py::test_overlap_and_claim_reads_agree_with_board` compares PR, lane and next-item results and snapshot counts. The shared review record verifies continuation for long claim rows and documents full overlap item detail through next().

- [Built in PR #150](https://github.com/mgd43b/plotroom/pull/150) at merge `3ecf20da2ba64a6e5e2e18f4e41b50db05b3fab0`.
- [Exact-main CI 45201121690](https://github.com/mgd43b/plotroom/actions/runs/45201121690), attempt 1, Python 3.14 and 3.15 passed.
