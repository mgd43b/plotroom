---
id: 4
type: story
title: "Embeddings off the writer"
parent: epic-search
covers: [R1, R4]
after: [1]
hitl: false
risk: high
tracker_id: "115"
remote: "https://github.com/mgd43b/plotroom/issues/115"
tracker_status: done
---

# Embeddings off the writer

## Description

An Embedder port (an HTTP endpoint or none), a resumable follower over seq that writes through the writer (entry 3's backfill reuses it), an indexer on it with its own index_seq, vectors as a BLOB column in a migrated table keyed by (record_id, model, dimension), queried with sqlite-vec's distance functions; changing the model reindexes. Tests serve a fake embedder over HTTP.

## Requirements

Verify: With the fake HTTP embedder blocking or down, writes commit at the same latency and indexing catches up when it returns; a restart resumes from index_seq; changing the configured model reindexes every record under the new model.

## References

- parent — _bmad-output/initiative-orchestration-control-plane/tickets/epic-search/epic-search.md
