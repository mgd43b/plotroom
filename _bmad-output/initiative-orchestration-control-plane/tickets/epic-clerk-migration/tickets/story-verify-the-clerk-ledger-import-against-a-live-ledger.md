---
id: 7
type: story
title: "Verify the clerk ledger import against a live ledger"
parent: epic-clerk-migration
covers: [R1]
after: [1.1, 2]
hitl: true
risk: medium
tracker_id: "195"
remote: "https://github.com/mgd43b/plotroom/issues/195"
tracker_status: done
---

# Verify the clerk ledger import against a live ledger

## Description

After the clerk spike records the live ledger's field shapes, compare an authorized read-only snapshot of an actual hand-run clerk ledger with the template and the merged importer. Keep the ledger private and carry a sanitized structural summary into the pilot preparation.

## Requirements

Verify: Account for each observed field and row type as imported, intentionally ignored by the existing contract, or needing separate disposition. Run a representative sanitized fixture against an isolated daemon twice and verify configured lanes, branch and channel, after-merge trigger links or checklist rows, and advisories; the second import changes nothing. Record only redacted findings and the verified revision. There are no GitHub writes or live clerk changes in the check.

## References

- parent — _bmad-output/initiative-orchestration-control-plane/tickets/epic-clerk-migration/epic-clerk-migration.md
- https://github.com/mgd43b/plotroom/issues/12
- https://github.com/mgd43b/plotroom/issues/109
- https://github.com/mgd43b/plotroom/pull/163
- coordination/clerk-plotroom.md
