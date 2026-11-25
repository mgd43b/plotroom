---
epic: epic-human-queue
date: 2026-11-25
verdict: accepted
criteria: declared
headless: true
---

# Epic 7 retrospective

## Epic summary

Reviewed main `75b30834c29e1be3c618af173d228b55c94e0ca5`. The epic's three requirements and four Done-when lines define the verdict. BMad lists eight stories, all with a built plan and done tracker state. Native #7 reports eight of eight children completed. The story inventory has no missing plans or unfinished tickets.

| BMad stories | GitHub stories | Work and verified delivery |
| --- | --- | --- |
| 7.1, 7.2, 7.3, 7.4 | #82, #84, #85, #86 | Verbatim decisions, ask records and checks, settlement. [PR #165, merge `fd50ef15`](https://github.com/mgd43b/plotroom/pull/165) · [Exact-main CI](https://github.com/mgd43b/plotroom/actions/runs/45241858028) |
| 7.5, 7.6 | #83, #87 | Goal grammar and evaluation, durable goals, GitHub milestone evidence. [PR #175, merge `e19bb1d9`](https://github.com/mgd43b/plotroom/pull/175) · [Exact-main CI](https://github.com/mgd43b/plotroom/actions/runs/45365178261) |
| 7.7, 7.8 | #88, #89 | Review follow-through and authenticated daemon acceptance. [PR #177, merge `48ec343e`](https://github.com/mgd43b/plotroom/pull/177) · [Exact-main CI](https://github.com/mgd43b/plotroom/actions/runs/45428167451) |

Compared the epic, `tickets.toml`, each story and plan, and the cited AD-2, AD-8, AD-13 and AD-18 sections of the architecture. No epic run folders or historical session transcripts were available to this review. The plans and merged PRs supply recorded review evidence. The baseline analyzer read `3ecf20da..78471ab1` (shared by 7.1–7.4), `78471ab1..88e2387e` (7.5–7.6), and `88e2387e..48ec343e` (7.7–7.8, inferred end at the acceptance merge). Shared baselines have no independent per-story range. The first two windows contain unrelated PRs. Scope each claim to the verified merge's first-parent diff and named tests. Do not add merge churn to the commit churn that it restates.

## Findings

- **Architecture and integration — accept.** Decisions and asks use the ledger's typed commands and writer authorization. Goal evaluation reads the projected GitHub evidence through the store port. `src/plotroom/core/operator_goals.py` and `goal_language.py` separate storage/evaluation from the HTTP or stdio adapter, and `tests/test_epic7_e2e.py` verifies both transports over the same daemon. The storage changes use sequential migration revisions `0021_human_queue` and `0023_operator_goals`. The range audit did not identify an added direct persistence adapter import in the changed core features. [Queue diff](https://github.com/mgd43b/plotroom/pull/165/files) · [Goals diff](https://github.com/mgd43b/plotroom/pull/175/files)
- **Review follow-through — accept.** The 7.7 plan records five answered and resolved threads on #165 and three on #175. The fixes cover exact ticket filtering, missing issues, deleted replies, batched closed-history reads and the comment index, per-issue retries, boundary probes, and selective goal evidence. The 7.8 plan separately records hardening of the closed-issue selector, wrong-comment citation, ask burial on update, configured stale threshold, answered closed issues, and dry goal reads. #177 has four more resolved threads. These checks span the queue-to-goal boundary and supply regression evidence for the earlier findings. [Review sweep and acceptance evidence](https://github.com/mgd43b/plotroom/pull/177/files)
- **Verification wording — accept the recorded fixture scope; propose a wording check for future suites.** Story 7.8 describes testing against “the test repository.” Its frozen acceptance plan specifies an isolated fixture repository, and the test drives deterministic GitHub read-adapter responses alongside the actual daemon. This proves the epic's GitHub projection and MCP behavior with fixtures. It does not establish access to a live GitHub repository or validate owner credentials. Keep the distinction when future suite requirements are drafted. [Story #89](https://github.com/mgd43b/plotroom/issues/89) · [Acceptance plan and harness](https://github.com/mgd43b/plotroom/pull/177/files)

The retrospective applied the adversarial, edge-case and verification-gap lenses inline to sourced queue-to-goal changes and their consumers. It checked invalid and missing citations, comment changes, issue selection and relationship evidence, authorization, and repeat reads across daemon restart. Churn was screened for growth and duplicated paths. The first window is shared with other epics, so broad file-size attribution was excluded. The suffix of #179 that updates the Epic 7 harness is covered by the fresh run below. No additional actionable runtime defect was substantiated in this scope.

## Behavior verification

On the reviewed tree, `uv run --locked pytest -q tests/test_epic7_e2e.py tests/test_search_e2e.py` ran disposable local projects, with 12 cases passing on Python 3.14.10. Epic 7 accounts for eight cases, one per criterion on each transport. The harness started the actual Plotroom daemon and exercised a normal HTTP connection and the stdio proxy. Provider responses were deterministic fixtures supplied to the real GitHub readers. No live repository was used.

| Epic criterion | Direct observation |
| --- | --- |
| Verbatim decisions and citation rejection | The recorded words round-tripped byte for byte, including normalization-sensitive text. Missing, wrong-author, wrong-comment and normalized citations failed without recording a decision. |
| Buried, optionless, stale and orphaned asks | All four appeared as separately attributable `next()` entries. A closed issue with a later human reply did not produce an orphan. |
| What remains and why | `goal()` returned exactly the remaining selected issues and the clauses they failed. |
| Persisted, read-only goal | The named goal survived daemon restart. Uncertain evidence was surfaced and dry evaluation left ledger sequence and SQLite data version unchanged. |

The #177 acceptance plan records all eight cases passing on both managed Python versions. Its exact-main CI also passed. [Acceptance run](https://github.com/mgd43b/plotroom/actions/runs/45428167451)

## Previous-retro follow-through

At the audited commit, the preceding Epic 6 has no retrospective on main. There are no prior action records to verify. The parallel audit draft does not establish historical follow-through.

## Action items

1. **Proposed process action, ticketer.** When an acceptance suite uses an isolated repository fixture, name that input in both the story and plan at refinement. Keep live owner verification explicit where required. This can ride in the common closure and evidence guidance. No new issue or rewrite of #89 is proposed.

## Decision records

The small goal language, projection source and exact-word behavior are already recorded in the epic Notes and architecture. No separate ADR is proposed or written.

## Acceptance verdict

**accepted**, using the declared criteria. All eight stories are finished. The observed behaviors cover the four Done-when lines and match the approved fixture scope stored in the 7.8 plan. No new evidence invalidates the historical completion.

## Open questions

No blocking acceptance question. Live GitHub credential validation was outside this exercise, and the evidence makes no claim about it.

## Assumptions

Headless epic reference 7 resolved to `epic-human-queue`. There were no pending or built-only stories. The evidence-based machine verdict does not claim a new owner decision. The proposed fixture-labeling practice has no approval or tracker write in this audit.
