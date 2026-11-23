---
id: 5
type: story
title: "Search by meaning"
parent: epic-search
covers: [R1, R4]
after: [4, 2]
hitl: false
risk: medium
tracker_id: "118"
remote: "https://github.com/mgd43b/plotroom/issues/118"
tracker_status: done
---

# Search by meaning

## Description

search() ranks by vector distance where an embedder is configured, and by full text otherwise, behind the same call; results stay within the token budget.

## Requirements

Verify: With the fake HTTP embedder, a paraphrase of a decision finds it with no word in common; with no embedder, the same call returns full-text results.

## References

- parent — _bmad-output/initiative-orchestration-control-plane/tickets/epic-search/epic-search.md
