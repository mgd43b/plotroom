---
id: 3
type: story
title: "Rule 4: stale CI and the automatic branch update"
parent: epic-merge-controller
covers: [R3]
after: [2, 2.15, 1.7]
hitl: true
risk: high
tracker_id: "67"
assignee: "mgd43b"
remote: "https://github.com/mgd43b/plotroom/issues/67"
tracker_status: backlog
---

# Rule 4: stale CI and the automatic branch update

## Description

A fetcher adds each PR's merge base and the files main changed since it; rule 4 refuses a PR with a file changed on both sides as stale (no merge base, or a file list that cannot be read, refuses); a GitHub write adapter that exposes only branch update and merge performs one automatic branch update by merging main, keyed branch_update:<pr>:<head_sha>, only when rules 1 to 3 pass with checks complete and outside a red main or a deploy window; a failed update (a conflict) opens an item for the lane; an allow-overlap label by a configured login waives rule 4 for that head. The owner grants contents and pull-request write to the PAT and the GitHub App.

## Requirements

Verify: A PR whose file changed on main is refused as stale and gets exactly one branch update per head, also across a restart, and only once rules 1 to 3 pass; a conflicting update opens one lane item; the write adapter has no call besides branch update and merge. On the authorized dedicated repository with its approved scoped permissions, re-verify #70's live/read verdict and audit log, #71's pinned merge offer, refusal and observed-merge outcomes, and #73's enabled test-project merge and disabled-mode guard. Review #74's deferred-finding dispositions against that live evidence. Keep adverse cases that require deterministic fixtures in their existing checks. Record a public-safe result for each issue and keep any live write confined to the separately authorized test scope.

## References

- parent — _bmad-output/initiative-orchestration-control-plane/tickets/epic-merge-controller/epic-merge-controller.md

## Notes

- Decision: 2026-11-22, this is the least certain story (the first GitHub write); it follows rules 2 and 3 because the update waits on them.
- Check beyond its criteria: a branch update never runs twice for one head even when two events arrive in one poll.
