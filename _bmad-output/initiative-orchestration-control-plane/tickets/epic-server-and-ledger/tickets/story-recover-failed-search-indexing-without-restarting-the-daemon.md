---
id: 24
type: story
title: "Recover failed search indexing without restarting the daemon"
parent: epic-server-and-ledger
covers: []
after: []
refined: false
hitl: false
risk: medium
tracker_id: "172"
assignee: "mgd43b"
remote: "https://github.com/mgd43b/plotroom/issues/172"
tracker_status: done
---

# Recover failed search indexing without restarting the daemon

## Description

A SQLite write failure can end a search indexing input. Rollback leaves the last committed checkpoint intact. The daemon currently resumes indexing from that checkpoint on restart.

## Requirements

Verify operator-visible failure and recovery during the same daemon run using search indexing as the case. After storage becomes available, resume from the last committed checkpoint without missing or duplicating indexed records. A persistent failure remains visible while retry behavior stays bounded.

## Notes

- Open decision: agree how the operator sees the failed input, what initiates recovery, and the retry and health policy. The initial acceptance is scoped to local search indexing.

## References

Evidence: [#115 foundation review](https://github.com/mgd43b/plotroom/blob/60ac5ffd247dd21fff701d066db55df480e53c91/_bmad-output/initiative-orchestration-control-plane/tickets/epic-search/tickets/story-embeddings-off-the-writer-plan.md#L83), [#119's merged runtime disposition](https://github.com/mgd43b/plotroom/blob/0df87521334e75a9a80262d857448241047cc4be/_bmad-output/initiative-orchestration-control-plane/tickets/epic-search/tickets/story-refactor-sweep-plan.md#L60).
