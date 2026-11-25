---
epic: epic-search
date: 2026-11-25
verdict: accepted
criteria: declared
headless: true
---

# Epic 10 retrospective

## Epic summary

Reviewed main `75b30834c29e1be3c618af173d228b55c94e0ca5`. The epic's four requirements and three Done-when lines define the verdict. BMad lists eight stories and bugs with built plans and done tracker states. Native #10 reports eight of eight children completed. Both findings re-filed from 10.6 are completed. #172 is a Server and ledger child and was verified as a dependency of the evidence, outside the epic's native child count.

| BMad stories | GitHub stories | Work and verified delivery |
| --- | --- | --- |
| 10.1, 10.4 | #114, #115 | Search and off-writer embedding foundation. [PR #164, merge `60ac5ffd`](https://github.com/mgd43b/plotroom/pull/164) · [Exact-main CI](https://github.com/mgd43b/plotroom/actions/runs/45238248125) |
| 10.2, 10.3, 10.5, 10.6, 10.7 | #116–#120 | Source indexing, historical backfill, semantic ranking, excerpt and evidence sweep, daemon acceptance. [PR #168, merge `0df87521`](https://github.com/mgd43b/plotroom/pull/168) · [Exact-main CI](https://github.com/mgd43b/plotroom/actions/runs/45284972395) |
| 10.8 and cross-epic follow-up | #171 and #172 | Permanent rejection isolation and restart-free recovery. [PR #179, merge `931e1c79`](https://github.com/mgd43b/plotroom/pull/179) · [Exact-main CI](https://github.com/mgd43b/plotroom/actions/runs/45444344091) |

Compared the epic, `tickets.toml`, eight child stories and plans, the #172 recovery plan, and the cited AD-5, AD-6 and AD-13 architecture. No epic run folders or historical session transcripts were available. The range analyzer read `169f3269..fd50ef15` (shared by 10.1 and 10.4), `fd50ef15..e5026784`, `e5026784..86cc0c3a`, `86cc0c3a..e59d434d`, `e59d434d..05b2af64`, `05b2af64..0df87521` for the #168 lane, and `995b3198..931e1c79` for 10.8. The last intervals for each lane end at the relevant verified merge. A shared baseline gives no independent earlier per-story interval. Some intervals contain merges of intervening main work, and #179 also includes the unrelated HTTPS story. First-parent PR diffs and named search files establish attribution. Merge churn restates much of the branch work and was not added to the branch totals.

## Findings

- **Architecture and transaction boundaries — accept.** Core retains the search and embedding ports. The lexical index is part of the writer's unit of work, while provider HTTP calls happen off the writer. Progress or a typed rejection is serialized back through the existing writer. FTS, vector state and rejection disposition stay inside the project's SQLite database. Migration 0024 follows 0023 and the verified populated upgrade preserved vectors and cursor. [Foundation diff](https://github.com/mgd43b/plotroom/pull/164/files) · [Recovery and schema diff](https://github.com/mgd43b/plotroom/pull/179/files)
- **Ranking, budget and formatting — accept.** The 10.6 review found bounded excerpts could omit part of a match that fit. #168 reserves the match span and verifies both a long token and a long phrase through the bounded SQLite path. Its MCP tests exercise the serialized budget and omitted count, preventing an excerpt-only correction from silently breaking the tool-level budget. The scoped churn review found distinct responsibilities for FTS SQL, embedding SQL, and core orchestration. [Review and regression diff](https://github.com/mgd43b/plotroom/pull/168/files)
- **Permanent record rejection and checkpoint recovery — remediated.** The two 10.6 findings were re-filed as #171 and #172. #179 stores a confirmed record-specific rejection with the model and source revision, retains the original full text, and lets later records be embedded. The recovery path resumes from committed checkpoints after classified transient storage failures. The tests distinguish ambiguous provider failure from a durable rejection and exercise the actual SQLite retry path in a running daemon. [#171](https://github.com/mgd43b/plotroom/issues/171) · [#172](https://github.com/mgd43b/plotroom/issues/172) · [Merged source and tests](https://github.com/mgd43b/plotroom/pull/179/files)

The retrospective applied the adversarial, edge-case and verification-gap lenses inline to source/backfill, model replacement, provider outcomes and storage recovery, including the relevant changes at the writer and MCP boundaries. It checked empty, pending, rejected and successful query paths, provider fallback, project scoping, cursor rollback and model changes. This was a scoped review of the proven Epic 10 changes and their consumers. It did not re-review unrelated HTTPS or toolchain work in #179. No additional actionable runtime defect was substantiated.

## Behavior verification

On the reviewed tree, `uv run --locked pytest -q tests/test_epic7_e2e.py tests/test_search_e2e.py` passed 12 cases on Python 3.14.10. Four Epic 10 cases used authenticated MCP over a real loopback HTTP listener and disposable project databases. Semantic scenarios used a local HTTP embedder fixture. No production provider was used.

| Epic criterion | Direct observation |
| --- | --- |
| Full-text excerpts scoped to the active project | Two tokens queried the same listener and saw only their own project IDs and excerpts. Long hits stayed within the serialized budget with the expected omitted count. |
| Semantic lookup and model reindex | A paraphrase found the prior decision. After model replacement the fixture observed lexical fallback during index rebuild, then confirmed ranking with a different vector space. |
| Historical backfill | A fixture database populated at pre-search revision 0017 upgraded to the current head. Authenticated MCP found the oldest historical record with its original ID and excerpt. |
| Re-filed permanent rejection | Confirmed rejected source text stayed lexical. A later record still ranked semantically and the rejected record did not count as a vector hit. |

The same reviewed tree passed 57 focused cases for source indexing, backfill, recovery, and embedding. The #172 plan separates the same-daemon retry for transient SQLite BUSY/FULL from the physical COMMIT constraint test, which verifies rollback and successful explicit resubmission on a fresh connection. The #164, #168 and #179 reviews had two, five and two threads respectively, all resolved. [#179 exact-main run](https://github.com/mgd43b/plotroom/actions/runs/45444344091)

## Previous-retro follow-through

At the audited commit, the preceding Epic 9 has no retrospective on main. There are no prior action records to verify.

## Action items

No new implementation or issue is proposed. The two existing follow-ups are complete, with their disposition retained in the refactor story.

## Decision records

The SQLite search port, optional embedder and model reindex are established in AD-6. The approved permanent-rejection choice and restart-free recovery policy are documented in #171/#172, their plans and the merged behavior. No separate ADR is proposed or written.

## Acceptance verdict

**accepted**, using the declared criteria. All eight native children are finished. The cross-epic recovery dependency is complete as well. The live local daemon and approved deterministic provider scenarios demonstrate the epic's acceptance; production embedder selection stays deferred.

## Open questions

No blocking acceptance question. Provider choice and validation for any eventual deployment remain outside this audit.

## Assumptions

Headless epic reference 10 resolved to `epic-search`. There were no pending or built-only stories. The evidence-based machine verdict does not claim a new owner decision. PR #179's search evidence is scoped to #171 and #172.
