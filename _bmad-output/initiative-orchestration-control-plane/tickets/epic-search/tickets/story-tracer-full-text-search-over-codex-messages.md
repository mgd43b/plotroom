---
id: 1
type: story
title: "Tracer: full-text search over Codex messages"
parent: epic-search
covers: [R1, R3]
after: [1.4, 2.7]
hitl: false
risk: medium
tracker_id: "114"
remote: "https://github.com/mgd43b/plotroom/issues/114"
tracker_status: done
---

# Tracer: full-text search over Codex messages

## Description

The thinnest path: the Search port, an FTS5 adapter writing rows for switchboard.message events inside the writer's unit of work (an Alembic migration hidden from autogenerate), and search(text, kinds?, limit) returning record ids, kinds and short excerpts within one project and its token budget, saying how many hits it left out.

## Requirements

Verify: A switchboard message is found by a word from it, results carry ids and excerpts only, a query matching more than the budget returns within it and says how many hits it left out, and a second project's token never sees the first project's records.

## References

- parent — _bmad-output/initiative-orchestration-control-plane/tickets/epic-search/epic-search.md
- _bmad-output/initiative-orchestration-control-plane/architecture-orchestration-control-plane/architecture-orchestration-control-plane.md, AD-6
