---
id: 5
type: story
title: "MERGE items, observed merges and refusals the lane sees"
parent: epic-merge-controller
covers: [R1, R5]
after: [13, 2, 3, 1.8, 1.9]
hitl: false
risk: medium
tracker_id: "71"
remote: "https://github.com/mgd43b/plotroom/issues/71"
tracker_status: backlog
---

# MERGE items, observed merges and refusals the lane sees

## Description

A MERGE verdict opens a state-backed item that closes when the live verdict for that head stops being MERGE; it is for the clerk (the orchestrator when no clerk is configured), and a labeled-fix MERGE is for the orchestrator only; its suggested action is to call merge_gate(pr) and, only if it returns MERGE for that head, run the full command with the configured merge method (merge by default) and --match-head-commit. Core logs MERGED with the PR's merge-commit SHA when it observes a merge, gated when an open MERGE item matched the head, else outside_gate with an item. A refusal opens an item for the owning lane's worker, deduplicated on PR, head and reason; a PR with no lane gets its refusal items for the orchestrator; draft PRs and PRs with the configured skip label are not gated.

## Requirements

Verify: The clerk runs a MERGE item's action and the merge is logged as gated; when a new unresolved thread lands after the item opened on the same head, the action's merge_gate refuses and no merge runs with its merge-commit SHA; a merge by hand is logged outside_gate with an item; a push or a freeze closes an open MERGE item; a refused PR's lane worker sees the refusal through wait() once per reason; a draft PR opens nothing.

## References

- parent — _bmad-output/initiative-orchestration-control-plane/tickets/epic-merge-controller/epic-merge-controller.md

## Notes

- Decision: 2026-11-22, the owner chose refusals as items the lane's own wait() sees ('Item the lane's wait() sees'), with no dependency on epic 3.
- Hands on: MERGE items and merge_gate to epic 9's clerk on plotroom (entry 9.3).
