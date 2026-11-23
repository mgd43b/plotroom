---
id: 1
type: story
title: "Tracer: plotroom import loads the clerk's triggers"
parent: epic-clerk-migration
covers: [R1]
after: [3.5, 3.6, 3.8, 1.5]
hitl: false
risk: medium
tracker_id: "108"
remote: "https://github.com/mgd43b/plotroom/issues/108"
tracker_status: backlog
---

# Tracer: plotroom import loads the clerk's triggers

## Description

The thinnest path: plotroom import reads a triggers.md in the clerk skill's template format and creates each exact trigger through the running server's commands as orchestrator; the trigger command gains an orchestrator-only imported_fired argument, declared in the writer-rule table, so a trigger marked FIRED is imported with fired state set; a prose trigger becomes a next item for the orchestrator to bind (AD-15). Messages with parts filled at fire time ([sha8], [N]) are imported as fixed text with a note on the trigger.

## Requirements

Verify: Importing a fixture triggers.md with one open, one FIRED and one prose trigger creates two exact triggers with the right states and one bind item, and importing it again changes nothing.

## References

- parent — _bmad-output/initiative-orchestration-control-plane/tickets/epic-clerk-migration/epic-clerk-migration.md
- .claude/skills/clerk/templates/triggers.md

## Notes

- Decision: 2026-11-22, fire-time fields in clerk messages are imported as fixed text; send stays static (epic 3 unchanged).
