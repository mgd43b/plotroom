---
title: "Ticket-tree moves against open PRs"
type: feature
ticket: 3
created: '2026-11-25'
status: built
---

## Provenance

Retrospective plan reconstructed on 2026-11-25 for BMad 5.3 and [GitHub #78](https://github.com/mgd43b/plotroom/issues/78). The [shared Epic 5 plan](story-tracer-a-conflicting-claim-raises-an-overlap-item-plan.md) captured the original task and review record. This file records the as-built evidence. No independent plan baseline or separate historical approval was established.

## Intent

Report one item per moving PR when a rename under the configured GitHub ticket tree overlaps files edited by other open PRs. Identify the distinct editing PRs and suggest editors merge before the mover when the dependency order is acyclic.

## Scope and acceptance

Use the repository-relative GitHub ticket-tree path and projected rename metadata. Untouched moves do not create an item. Clear the item when facts change. Surface cyclic editor dependencies for coordination without suggesting an order.

## Implementation evidence

The shared plan’s #78 task specifies the item and order. PR #150 implements the move reconciliation in `core/pull_overlaps.py`; commits `097dcb8` and `11bdf4dc` refine cycle handling and transitive editor order.

## Verification

`tests/test_claims_e2e.py::test_ticket_tree_move_lists_editing_prs_first` exercises multiple editors, an untouched path and changed PR facts. The local-filesystem root-mapping and move-against-move scope questions are recorded in the shared Review Triage Log.

- [Built in PR #150](https://github.com/mgd43b/plotroom/pull/150) at merge `3ecf20da2ba64a6e5e2e18f4e41b50db05b3fab0`.
- [Exact-main CI 45201121690](https://github.com/mgd43b/plotroom/actions/runs/45201121690), attempt 1, Python 3.14 and 3.15 passed.
