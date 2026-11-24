---
id: 8
type: bug
title: "Permanent embedding rejection stalls vector indexing"
parent: epic-search
covers: [R1, R4]
after: []
refined: false
hitl: false
risk: medium
tracker_id: "171"
remote: "https://github.com/mgd43b/plotroom/issues/171"
tracker_status: done
---

# Permanent embedding rejection stalls vector indexing

## Description

A permanently rejected record keeps the configured model and dimension at the same durable checkpoint. Valid records after it stay pending. Queries that need the incomplete vector index fall back to lexical results.

## Requirements

Verify that valid records after a permanently rejected record can progress under the agreed record-handling policy. Preserve transient retry, durable checkpoint integrity, and lexical availability during incomplete indexing.

## Notes

- Open decision: choose how rejected input is handled and when checkpoint advancement is permitted. The current search index uses the source text unchanged. This ticket records the behavior and decision for refinement.

## References

Evidence: [#115 foundation review](https://github.com/mgd43b/plotroom/blob/60ac5ffd247dd21fff701d066db55df480e53c91/_bmad-output/initiative-orchestration-control-plane/tickets/epic-search/tickets/story-embeddings-off-the-writer-plan.md#L82), [#119's merged rejection disposition](https://github.com/mgd43b/plotroom/blob/0df87521334e75a9a80262d857448241047cc4be/_bmad-output/initiative-orchestration-control-plane/tickets/epic-search/tickets/story-refactor-sweep-plan.md#L59).
