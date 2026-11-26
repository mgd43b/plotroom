---
id: 3
type: story
title: "Checks, runs and image builds on main"
parent: epic-one-inbox
covers: [R1]
after: [14]
hitl: true
risk: medium
tracker_id: "33"
remote: "https://github.com/mgd43b/plotroom/issues/33"
tracker_status: backlog
---

# Checks, runs and image builds on main

## Description

A fetcher adds checks per head SHA and workflow runs on the default branch, including the configured image-build checks, each state change a new fact (<check_run_id>@<status>/<conclusion>), with each run's job conclusions, into the projection, which reports the latest run per check name. The owner adds a CI workflow with an image-build job to the test repository. Hands on: main's verdict and image checks to epics 3 and 4.

## Requirements

Verify: A CI run on a PR and on main produces one event per check state change, a rerun on the same head is a new fact, and the projection reports main's latest completed verdict and its image-build checks. Use an existing same-head rerun in the authorized dedicated repository for the rerun check. If none is available, record the missing evidence and leave that check pending. Using the authorized dedicated repository and its identified CI and image-build workflow, re-verify #34's review-thread projection and #56's green-check, resolved-thread, and closed-issue triggers. Verify #57's lane report and same-commit main/image readiness, #63's test-PR gate verdict and as-of, and #65's flake-candidate outcomes against suitable recorded run history without rerunning jobs. Retain the deterministic negative cases. Record a public-safe result or a specific missing live input for each issue.

## References

- parent — _bmad-output/initiative-orchestration-control-plane/tickets/epic-one-inbox/epic-one-inbox.md
- Built: https://github.com/mgd43b/plotroom/pull/147

## Notes

### Live fixture observations (2026-11-26)

- Plotroom’s observer passed on main `1db8153cc26422e39aca82c13f7835974d595d42`. It observed four check events for fixture PR #2 at head `3b130ec9573e8ac6829bc81af85c346fb79bdeb8` and two for fixture main at `9a6ce8a89a15046c18dbfa5d7d2af88bb0edbc14`. An unchanged follow-up poll produced no new check events. The PR runner used synthetic merge `79600d84`. The named fixture jobs were observed at completion. Their queued and in-progress states were missed. Kody was observed transition from in progress to success. [Observer run](https://github.com/mgd43b/plotroom/actions/runs/45859522091) · [PR checks](https://github.com/mgd43b/plotroom-test/actions/runs/45859680348)
- #34 observed one inline review thread opening and resolving, with none remaining unresolved. #56 observed one firing each for `pr_green`, `pr_threads_resolved`, and `issue_closed`. Fixture issue #3 was closed. [Review thread](https://github.com/mgd43b/plotroom-test/pull/2#discussion_r4711152479) · [Fixture issue](https://github.com/mgd43b/plotroom-test/issues/3)
- #57 observed the owning lane’s report and refusal of the foreign lane. `run_finished` and `main_green_with_images` each fired once. The main verdict and successful image job were tied to the same fixture main tip. [Main CI](https://github.com/mgd43b/plotroom-test/actions/runs/45846161353) · [Image job](https://github.com/mgd43b/plotroom-test/actions/runs/45846161353/job/140000271767)
- #63’s read-only gate returned `REFUSE` with `overlap_unreadable` at `2026-11-26T16:33:15.343042Z` on the exact PR head above. This run records the refusal. Its cause remains open for separate investigation. [Observer run](https://github.com/mgd43b/plotroom/actions/runs/45859522091)
- **Pending:** #33 still needs an existing same-head rerun and #65 still needs qualifying recorded failure history. The fixture PR was left unmerged and closed, its issue was closed and thread resolved, and only the run-owned branch was cleaned up. Fixture main stayed unchanged. The observer produced zero artifacts. [Fixture PR](https://github.com/mgd43b/plotroom-test/pull/2)
