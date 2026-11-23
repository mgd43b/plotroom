---
id: 14
type: bug
title: "The clerk's gate merges without completed review evidence"
parent: epic-merge-controller
covers: []
after: []
refined: false
hitl: false
risk: medium
---

# The clerk's gate merges without completed review evidence

## Description

Bootstrap maintenance, found by the coordinator. The copied clerk gate (.claude/skills/clerk/scripts/safemerge.sh) does not check that the configured Kody GitHub app has finished reviewing the PR's head. The clerk enforces this by hand today. The fix makes the gate fail closed per PR unless it reads completed review evidence from the configured Kody app for the pinned head. A skipped result alone or a progress comment is not enough, and a completed run with no findings passes. The existing pinned-head CI, thread and main checks stay as they are. This is a project-specific edit to the copied skill, which coordination/README.md allows when a rule really differs here.

## Requirements

Verify: Under ORCH_DRY=1 on test PRs, the gate refuses a PR with no Kody review of its head, with only a skipped result, or with only a progress comment. It passes a PR with a completed Kody run, with findings resolved or with none. Its CI, thread and main refusals are unchanged.

## References

- parent — _bmad-output/initiative-orchestration-control-plane/tickets/epic-merge-controller/epic-merge-controller.md
- .claude/skills/clerk/scripts/safemerge.sh
- .claude/skills/clerk/scripts/lib.sh
- coordination/README.md

## Notes

- Open question for the coordinator: safemerge.sh is epic 4's reference (R1, R2), and plotroom's own gate gains no matching rule unless one is added. Adding one is a new requirement on epic 4.
- Follow-up outside this repository: the same change may belong upstream in mgd43b/prompts.
