---
id: 2
type: story
title: "plotroom import loads the clerk's ledger"
parent: epic-clerk-migration
covers: [R1]
after: [1, 1.8, 2.15, 6.2]
hitl: false
risk: medium
tracker_id: "109"
remote: "https://github.com/mgd43b/plotroom/issues/109"
tracker_status: done
---

# plotroom import loads the clerk's ledger

## Description

plotroom import reads a ledger.md in the clerk skill's template format: each open PR's owner and channel become a lane for its configured worker through lane_start, its branch taken from the PR's head branch in the projection (AD-16); an After-merge entry naming a trigger links to the imported trigger, and any other becomes a checklist row through an orchestrator-only checklist_import(lane, rows) declared in the writer-rule table; a lane with no configured worker, and each PR on the skip list, opens an item for the orchestrator (plotroom writes no GitHub labels); the flakes-sent table is not imported (epic 4 recomputes flake candidates). The clerk spike (epic 1, #12) says what live ledgers hold beyond the template.

## Requirements

Verify: Importing a fixture ledger.md creates its lanes for configured workers with their branches and channels, its After-merge steps as checklist rows or trigger links, one item for an unconfigured worker and one per skipped PR, and a second import changes nothing.

## References

- parent — _bmad-output/initiative-orchestration-control-plane/tickets/epic-clerk-migration/epic-clerk-migration.md
- .claude/skills/clerk/templates/ledger.md

## Notes

- Unknown: What live clerk ledgers hold beyond the template; the clerk spike (epic 1 entry 1, #12) answers.
