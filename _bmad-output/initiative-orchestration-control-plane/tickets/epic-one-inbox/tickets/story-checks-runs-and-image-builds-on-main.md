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
assignee: "mgd43b"
remote: "https://github.com/mgd43b/plotroom/issues/33"
tracker_status: done
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
- The fixture PR was left unmerged and closed, its issue was closed and thread resolved, and only the run-owned branch was cleaned up. Fixture main stayed unchanged. The observer produced zero artifacts. [Fixture PR](https://github.com/mgd43b/plotroom-test/pull/2)

### Same-head workflow rerun (2026-11-27)

- Decision (2026-11-27, coordinator chat): Matt authorized this one green-to-green fixture rerun: “why are we waiting for one to happen? rerun a passing workflow on the same head in plotroom-test and get #33 moving.”
- Both push attempts in fixture run `45846161353` passed on `refs/heads/main` at unchanged head `9a6ce8a89a15046c18dbfa5d7d2af88bb0edbc14`. Attempt 1 had check IDs `140000271234` and `140000271767`. Attempt 2 had distinct check IDs `140607557039` and `140607557509`. [Attempt 1](https://github.com/mgd43b/plotroom-test/actions/runs/45846161353/attempts/1) · [Attempt 2](https://github.com/mgd43b/plotroom-test/actions/runs/45846161353/attempts/2)
- A disposable offline replay fed the recorded GitHub responses through the normal pollers on Plotroom `88058c40f67ffc995364cf42a9b8af0de434528e`. It produced two distinct workflow-attempt events and four distinct check events. An unchanged third poll added no events. The projection reported attempt 2 green with image job `140607557509`. Scratch resources were removed. [Plotroom main CI](https://github.com/mgd43b/plotroom/actions/runs/46021546372) · [Attempt 2 image job](https://github.com/mgd43b/plotroom-test/actions/runs/45846161353/job/140607557509)

### PR and main check transitions (2026-11-27)

- Fixture PR #5 attempt 1 ran at source head `d58ac0958b8cb0411c08029dd1fd9218fc125b84` (synthetic runner merge `fc4111ac`). Both named jobs were observed queued and completed successfully, and the image job was observed in progress. The regular check's in-progress state was missed. Replaying the recorded provider responses through the normal Plotroom projection produced five distinct check events and zero on repeat. The check IDs were `140623694080` and `140623694328`. The PR was closed unmerged and its temporary branch removed. Fixture main and the workflow stayed unchanged. [PR #5](https://github.com/mgd43b/plotroom-test/pull/5) · [PR run](https://github.com/mgd43b/plotroom-test/actions/runs/46033714518)
- Main attempt 3 was a push workflow attempt on the same `refs/heads/main` and head `9a6ce8a89a15046c18dbfa5d7d2af88bb0edbc14`. Both named jobs, `140626663247` and `140626663921`, were observed queued, in progress, and completed successfully. Their IDs differed from attempt 2. Offline replay of saved provider responses through the normal projection produced two workflow-attempt events and eight check events, including six new transition facts, with no duplicates. Attempt 3 was the latest green main verdict, with image job `140626663921`. [Main attempt 3](https://github.com/mgd43b/plotroom-test/actions/runs/45846161353/attempts/3) · [Image job](https://github.com/mgd43b/plotroom-test/actions/runs/45846161353/job/140626663921)
- Verify calls for a PR case and a main case with one event per observed check state change. The PR image job and both main jobs provide full cases. The prior #34, #56 and #57 observations stand. #63 has a recorded verdict and `as_of`. The cause of `overlap_unreadable` remains unconfirmed.
- **#65 missing live input:** The dedicated fixture has no suitable recorded failure history to verify a flake candidate or its check-name and run-link fallback. The deterministic cases, including negative and fallback cases, remain covered. Verify permits recording the specific missing live input. #65 has no new live result from this exercise.
