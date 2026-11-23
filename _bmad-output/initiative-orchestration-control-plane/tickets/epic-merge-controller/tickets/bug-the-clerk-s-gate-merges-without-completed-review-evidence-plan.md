---
title: "Require completed Kody review evidence in the clerk gate"
type: bugfix
ticket: 14
created: 2026-11-23
status: built
baseline_revision: eea6008a171abe60b2dfe459a0f91e0a1a542a2b
route: full
route_source: auto
risk: high
review: quick
review_source: pinned
lenses_ran: [quick, provenance, regression]
review_loop_iteration: 0
context: []
---

<frozen-after-approval reason="human-owned intent">

## Intent

The bootstrap clerk can pass a pull request whose ordinary checks are green before Kody has completed its review. Require authentic Kody evidence as an independent gate and bind PR CI to the SHA that the clerk would merge.

## Boundaries & Constraints

Keep the copied Bash gate compatible with Bash 3.2 and macOS. Preserve main CI, overlap, authorization, closing-keyword, and pinned-merge behavior. Scope this change to the copied clerk scripts, offline tests, and this plan. Leave the running clerk and Plotroom's merge controller to their owning lanes.

## I/O & Edge-Case Matrix

| Scenario | Input | Expected gate result |
|---|---|---|
| Completed current review | Configured App reports review success on the pinned head, including an explicit no-findings review | Continue to remaining gates |
| Cadence skip | Configured App reports a verified skip on the pinned head, the same PR has authentic completed opening review evidence, and every thread is resolved | Continue to remaining gates |
| Review unavailable | Missing, unreadable, forged, pending, progress-only, stale, or unrecognized evidence | Refuse |
| Incomplete review | Current skip without completed opening evidence, or any unresolved review thread | Refuse |
| CI incomplete | Prior-head CI, failed or pending latest run, or either Python job absent | Refuse |
| Head moves | PR advances during evaluation | Refuse or reject at the existing pinned merge operation |

</frozen-after-approval>

## Code Map

- `.claude/skills/clerk/scripts/safemerge.sh` reads PR metadata and review threads, checks main and overlap, and passes the expected head to the merge command.
- `.claude/skills/clerk/scripts/lib.sh` handles repository settings and derives the base CI verdict. Keep those shared behaviors.
- `.github/workflows/ci.yml` defines the two PR jobs, `check (python 3.14)` and `check (python 3.15)`.

## Tasks & Acceptance

- [x] Clerk gate scripts. Validate the current PR CI workflow run and attempt jobs against the pinned SHA and retain the existing pending/failure checks. Read the configured Kody App evidence through GitHub and require one of the two completed-review paths. Refuse missing or incomplete records and verify all review threads, including the PR head reported with the thread evidence.
- [x] Offline clerk fixtures. Stub both `gh` and `git` with GitHub-shaped data and run the real gate with `ORCH_DRY=1`. Exercise both permitted paths and the refusals above, including identity spoofing, wrong PR or SHA, and truncated or malformed evidence.
- [ ] Canonical plan. Record independent review and dry-run results and add the Built link to the lane PR.

Given readable evidence for one permitted review path, a green main, successful current Python jobs, and all existing gates satisfied, when the clerk dry-runs the PR, then it names the pinned SHA and exits 0. Given missing or invalid evidence, when the same command runs, then it exits with a refusal before reaching the merge command.

## Implementation Notes

- The PR gate validates the newest Actions run for the two Python jobs, then confirms the attempt is still current after reading the jobs. The existing main CI selector still handles the base branch.
- The Kody evidence gate checks the configured App identity, terminal conclusion, and exact summary. If the check omits its PR association, GitHub must identify the target PR uniquely on that commit and the check must have started at or after PR creation.
- A cadence skip needs a trusted opening SHA, the App's completed success on that SHA, and a reply on each resolved review thread. A final PR head read also protects the dry-run result.

## Plan Change Log

## Review Triage Log

- High, patch. An empty Kody PR association can allow a different PR's check on a shared SHA. Require GitHub to identify the target PR uniquely when the check itself omits its association.
- Medium, patch. A newer CI run explicitly associated with a different PR can obscure valid CI for the target PR. Exclude explicitly unrelated runs before selecting the latest and retain ambiguous runs as possible blockers.
- Medium, patch. The REST workflow path accepts the filename or ID, while `ORCH_CI_WORKFLOW` also accepts a display name for main CI. Use Plotroom's `ci.yml` for the PR query.
- Medium, patch. A head move after the thread snapshot can yield a stale dry run. Recheck the PR head at the end and retain GitHub's pinned merge argument.
- High, patch. A CI rerun can supersede a successful attempt during the job read. Confirm the latest run and attempt again before accepting success.
- Medium, patch. A resolved thread can have no reply. In the cadence-skip path, verify both a reply and a resolved thread.
- High, patch. GitHub's commit association can omit a prior closed or moved PR. For Kody checks with no direct PR association, reject checks started before the target PR opened and require valid GitHub timestamps.
- Three independent native reviewers checked the implementation. Follow-up review verified the corrections. GitHub's indirect association still cannot distinguish historical PRs it omits if their check times overlap the target PR.

## Design Notes

- The coordinator supplied sanitized check runs for the installed GitHub App `kodus-mgd43b` (ID `5822892`). The check is named `Kody Code Review`. A completed successful review reports “Review finished successfully. Suggestions (if any) were posted as PR/file comments.” A completed cadence skip reports “Manual mode requires @kody start-review.”
- The Checks API can return an empty `pull_requests` array for genuine review runs. A cadence skip therefore needs an independently established opening SHA for the same PR. The gate then reads the successful App check directly from that commit.
- Use `$OR_STATE_DIR/kody-opening-heads`, with one PR number and full SHA per line. The coordinator or clerk records the SHA observed when the PR opened. Require exactly one valid row for the PR and a different SHA from its current head. Refuse contradictory PR associations from GitHub and accept an empty association list when the SHA is otherwise established. The gate only reads this file.
- Request bounded GitHub lists with declared totals and refuse partial lists. Preserve the existing maximum of 100 review threads, validating the GraphQL envelope, PR head, count, and resolution state. The fixtures must replace both `git` and `gh`, run under Bash 3.2, and reject any unexpected invocation. Keep the active clerk checkout and state untouched.

## Verification

- Run `/bin/bash -n` on the changed shell scripts and execute the fixture suite with `/bin/bash` from the checkout.
- Bash 3.2 syntax, ShellCheck, and all 84 offline fixture cases passed.
- After the clerk merges, verify the merge commit is on main, both Python CI jobs are green on that commit, and the same fixture suite passes from that exact main revision.
