---
id: 2
type: story
title: "Rule 3: main's verdict and the marked fix"
parent: epic-merge-controller
covers: [R3, R4]
after: [1, 11, 6, 2.15]
hitl: false
risk: medium
tracker_id: "66"
remote: "https://github.com/mgd43b/plotroom/issues/66"
tracker_status: done
---

# Rule 3: main's verdict and the marked fix

## Description

Rule 3 as lib.sh's main-verdict rules: the configured CI workflow's runs on main decide; canceled and skipped runs are ignored; a failure run whose jobs were all canceled is no verdict and the next older run decides; startup_failure is red; red needs a failed or timed-out job, and unreadable jobs are not green; no verdict in the window refuses; a flake candidate on main is not red. A PR carrying the configured fix label, applied by a configured login, overrides only main red: rules 1, 2, 4 and 5, a missing or unreadable verdict and the deploy window still refuse it.

## Requirements

Verify: Fixture main histories give the same verdict as lib.sh for each case; with main red only the labeled fix passes, a label from another login does not count, and the labeled fix is still refused for an unresolved thread and for no main verdict.

## References

- parent — _bmad-output/initiative-orchestration-control-plane/tickets/epic-merge-controller/epic-merge-controller.md
- .claude/skills/clerk/scripts/lib.sh
