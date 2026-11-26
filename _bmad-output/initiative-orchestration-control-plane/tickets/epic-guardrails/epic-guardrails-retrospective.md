---
epic: epic-guardrails
date: 2026-11-26
verdict: accepted-with-open-items
criteria: declared
headless: true
---

# Guardrails retrospective

## Epic summary

Audited exact main `889b520e6731eb7b52b560b37d20d3b50478a545`. The recorded push CI passed Python 3.14, Python 3.15, image, and Helm on attempt 1. This retrospective records the evidence and the subsequent owner decision. The tracker update follows verified publication of this record. [Exact-main CI](https://github.com/mgd43b/plotroom/actions/runs/45885621097)

The [epic](epic-guardrails.md), [memlog](.memlog.md), ticket table, all seven stories and plans, and verified merge records were inventoried. The epic has no separate run-evidence folder. No ticket remains unfinished. All seven plans remain at `built` while the canonical story status is `done`; the seven native issues are independently verified Closed as completed.

| Ticket in build order | Canonical plan | Built evidence |
| --- | --- | --- |
| [#101](https://github.com/mgd43b/plotroom/issues/101) Budget tracer | [Plan](tickets/story-tracer-a-budget-raises-items-at-50-and-80-of-its-cap-plan.md) | Literal Built link to [#160](https://github.com/mgd43b/plotroom/pull/160), merge `43d5a1e7`. |
| [#102](https://github.com/mgd43b/plotroom/issues/102) OpenRouter meter | [Plan](tickets/story-first-provider-usage-adapter-openrouter-plan.md) | Built status, verified [#208](https://github.com/mgd43b/plotroom/pull/208) merge `bf96c117`. No literal Built backlink. |
| [#103](https://github.com/mgd43b/plotroom/issues/103) Stop at 100% | [Plan](tickets/story-stop-at-100-plan.md) | Literal Built link to [#160](https://github.com/mgd43b/plotroom/pull/160). |
| [#104](https://github.com/mgd43b/plotroom/issues/104) Budget ratio leaf | [Plan](tickets/story-the-budget-ratio-trigger-leaf-plan.md) | Literal Built link to [#160](https://github.com/mgd43b/plotroom/pull/160). |
| [#105](https://github.com/mgd43b/plotroom/issues/105) Hands-on routes | [Plan](tickets/story-routing-hands-on-requests-plan.md) | Literal Built link to [#160](https://github.com/mgd43b/plotroom/pull/160). |
| [#106](https://github.com/mgd43b/plotroom/issues/106) Refactor sweep | [Plan](tickets/story-refactor-sweep-plan.md) | Built status, verified [#209](https://github.com/mgd43b/plotroom/pull/209) merge `889b520e`. No literal Built backlink. |
| [#107](https://github.com/mgd43b/plotroom/issues/107) Done-when suite | [Plan](tickets/story-end-to-end-suite-over-the-epic-s-done-when-plan.md) | Built status, verified [#209](https://github.com/mgd43b/plotroom/pull/209). No literal Built backlink. |

### Range and evidence boundaries

| Plans | Derived range | Interpretation |
| --- | --- | --- |
| #101/#103/#104/#105 | `c5cbfdca5cabcca23d22ad8e74e33a6b1a297de6..2c30e23c9064640c1952f411a4e22eb87086f811` | Shared baseline, with 181 commits and 46 of 60 merges measured by the pre-pass. Contains unrelated concurrent work. #160's first-parent delta is the shared implementation evidence; [#207](https://github.com/mgd43b/plotroom/pull/207), merge `5caab6bd`, is the incomplete-sample follow-up. No artificial ticket-by-ticket division was inferred. |
| #102 | `2c30e23c9064640c1952f411a4e22eb87086f811..bf96c117ef78dfbedb2ca050140b763d651bc95e` | Four commits and one measured merge after the canonical story update. |
| #106 | `bf96c117ef78dfbedb2ca050140b763d651bc95e..b665731a6ca87168161fa450c1d0253fa7e72705` | One documentation commit. |
| #107 | `b665731a6ca87168161fa450c1d0253fa7e72705..889b520e6731eb7b52b560b37d20d3b50478a545` | Final range ends at the audit HEAD, inferred from the last plan. Three commits and one measured merge. |

The aggregate and lens review used the ordered first-parent production and test deltas of #160, #207, #208, and #209, checking later changes against the exact checkout. This excludes unrelated lanes and counts each patch once. Merge delta volume is reported separately from the plan-range pre-pass. Full historical session logs for every ticket were unavailable, so process conclusions use plans, PR evidence, and the recorded verification and handoff for this lane.

## Findings

### Aggregate and spec reconciliation

- **Architecture and size · accept as-is.** Guardrails add a read-only usage port, a provider adapter, budget and routing rules in core, and daemon composition. The AST import check on changed component roots found those dependencies in the existing direction. Across the four scoped merge deltas, 25 production paths and 12 test paths changed. The largest production changes were core budgets (+192/−10) and OpenRouter (+151/−0); the final modules are 182 and 151 lines. Larger files such as config and MCP gained focused parsing and tool wiring, so change volume alone does not establish a new oversized class. [Budgets](../../../../src/plotroom/core/budgets.py) · [Adapter](../../../../src/plotroom/adapters/openrouter.py) · [Daemon](../../../../src/plotroom/app/serve.py)
- **Reuse and pattern · accept as-is.** Routes, threshold items, and stops reuse the existing writer, receipts and actions. The single cumulative observation port still serves both the fixture and OpenRouter; #209 reuses the scripted relay, native drain and MCP harness. No separate paid or hosted implementation was introduced for acceptance. [Routing](../../../../src/plotroom/core/routing.py) · [Budget writer](../../../../src/plotroom/core/budgets.py) · [Fixture suite](../../../../tests/test_epic8_e2e.py)
- **Provider and cap semantics · accept the documented contract.** The verified provider cap can be lower than the local budget cap. OpenRouter may refuse further spend before the local 100% threshold is reached. The threshold follows recorded spend against the local cap. OpenRouter `as_of` records when Plotroom accepted a sample; it is no provider settlement watermark. Equal/lower OpenRouter counters hold both fields. Explicitly incomplete samples are ignored across meters, and complete authoritative downward corrections remain available to other meters. The local key pin and opaque reservation enforce attribution within one daemon data directory; operators keep keys dedicated across separate directories. [Cap and polling contract](../../../../README.md#track-openrouter-spend) · [Writer semantics](../../../../src/plotroom/core/budgets.py) · [#207](https://github.com/mgd43b/plotroom/pull/207)
- **Routing semantics · accept as-is.** A routed lookup returns the authored lane ID, including assignments prepared for a future lane. A missing assignment opens a durable orchestrator item. Existing fixtures cover seeded routes, restart, and authorization. [Route implementation](../../../../src/plotroom/core/routing.py) · [Routing fixtures](../../../../tests/test_routing.py)

### Review-lens findings and dispositions

- **Adversarial and edge case · defer hosted fencing and retain the existing handoff.** A bridged lane-pinned stop is surfaced as a failed action for operator attention. The #106 plan records the referral to the ticketer and the existing hosted-bridge design candidate. Local retry uses stable action keys; once-only effects allow transport retries. An active native drain lease issued before archival can still be completed after reassignment. This is an in-flight boundary to account for in any later fencing design, beyond the successful local scripted delivery exercised here. [Actual handoff disposition](tickets/story-refactor-sweep-plan.md#implementation-notes) · [Hosted behavior](../../../../docs/running.md) · [Native lease tests](../../../../tests/test_budgets.py)
- **Edge case · defer final accounting hardening.** Archived OpenRouter polls are eligible for two minutes after the durable archive event. Downtime or a tick interval longer than the window can leave no final observation. The key stays reserved. This is best-effort archive accounting; the live provider witness and fixture thresholds do not establish final settlement of all delayed charges. [Archive query](../../../../src/plotroom/adapters/store_sqlite/budgets_sql.py) · [Archive and restart test](../../../../tests/test_openrouter.py) · [Tick scheduler](../../../../src/plotroom/app/runtime.py)
- **Edge case · defer unmetered-budget visibility.** Generic budget registration accepts a syntactically valid provider label, so a mistyped label can produce a budget without an active meter. Normal OpenRouter registration checks the configured run and mounted key. Scope the acceptance to correctly wired meters and consider an operator-visible unmetered state or registration guard. [Budget registration](../../../../src/plotroom/core/budgets.py) · [Daemon meter wiring](../../../../src/plotroom/app/serve.py)
- **Verification gap · defer two targeted regressions.** Existing OpenRouter tests cover readable inactive databases and runtime target-reader locks, corruption and cancellation. They do not directly exercise startup with an unexpected nonempty inactive schema. Generic role denial tests also do not assert that a worker's denied registration with a configured OpenRouter run issues zero authenticated GETs. The startup path fails closed and current command authorization precedes preflight; the follow-up pins those behaviors. [Startup reservation check](../../../../src/plotroom/app/serve.py) · [Command authorization and preflight](../../../../src/plotroom/core/ledger.py) · [Existing OpenRouter tests](../../../../tests/test_openrouter.py)
- **Adversarial traceability · defer metadata-only correction.** Three built plans lack literal Built backlinks, although their merges and native completion are verified above. Preserve that distinction when using plans as gate evidence. [Canonical plans](tickets/) · [Provider merge](https://github.com/mgd43b/plotroom/pull/208) · [Acceptance merge](https://github.com/mgd43b/plotroom/pull/209)

## Behavior verification

| Declared check | Recorded result |
| --- | --- |
| R1, credential-backed cumulative spend and observation time | The exact #208 merge `bf96c117` ran 54 focused tests and a disposable configured daemon/MCP check. Its authenticated same-key GET recorded **USD $0.0000033**, matching the earlier authorized response, generation charge, and key usage delta. The historical key read verified a **USD $5 non-resetting key limit**; both billing records reported no BYOK. The exact merged verification scanned database files, status, logs and public artifacts for the key and private pin and found neither. The trial measured ordinary billing under the configured cap; it did not attempt cap exhaustion. [Provider plan](tickets/story-first-provider-usage-adapter-openrouter-plan.md#implementation-notes) · [#208 exact merge CI](https://github.com/mgd43b/plotroom/actions/runs/45880585298) |
| R2 and Done when 1, a run raises 50%/80% items and sends stop at 100%, once each | The exact #209 merge `889b520e` passed the daemon fixture test. At 49%, 79%, and 99% the matching effects were absent. At the exact thresholds both runs produced the expected keyed items, and the local scripted Switchboard and native drain stand-in received their respective keyed stop. A newer above-cap observation after restart advanced status and as-of while producing no duplicate threshold events or stop. The typed `budget_ratio` tests cover the level leaf, restart and replay, with legitimate re-crossing for `every` under a complete downward correction. [Daemon acceptance](../../../../tests/test_epic8_e2e.py) · [Ratio tests](../../../../tests/test_budget_ratio.py) · [#209 exact merge CI](https://github.com/mgd43b/plotroom/actions/runs/45885621097) |
| R3 and Done when 2, a routed hands-on request returns its lane and a miss becomes a next item | The second exact #209 daemon/MCP test verified the authored lane, no unrouted item for the match, and one orchestrator next item across repeated misses. [Daemon routing acceptance](../../../../tests/test_epic8_e2e.py) · [#209 exact merge CI](https://github.com/mgd43b/plotroom/actions/runs/45885621097) |

The recorded #209 detached verification passed both acceptance tests plus the startup-cancellation regression. This headless run inspected those exact-commit results and test code. It did not rerun a daemon, send live relay traffic, or repeat the provider trial. [#107 plan and verification](tickets/story-end-to-end-suite-over-the-epic-s-done-when-plan.md#verification)

## Previous-retro follow-through

The previous [Epic 7 retrospective](../epic-human-queue/epic-human-queue-retrospective.md#action-items) proposes that the ticketer label isolated fixture inputs in stories and plans and preserve explicit live owner verification. #107's story and plan identify the local fixture inputs; #102 separately names real provider evidence. This is evidence of explicit labeling here. No evidence was found of a general change to the shared closure guidance.

## Action items

The owner approved the tracker disposition. The three follow-up items remain deferred.

1. **Owner and ticketer, closure policy.** After the retrospective and canonical completion land with green exact-main CI and the coordinator releases the tracker sync, remove only the native blocker links from #8 to #1 and #2. Retain closed #3 and all child links. Mirror the approved Epic #8 Notes, remove only the backlog label, and mark #8 completed. Verify the resulting body, labels, state, and links.
2. **Ticketer, traceability.** Carry the verified #208 and #209 backlinks for #102, #106, and #107 into an authorized metadata pass.
3. **Guardrails test owner, proposed regressions.** Pin fail-closed startup with an unexpected inactive schema and worker authorization before configured provider preflight. Use synthetic mounted keys and assert zero authenticated reads on denied or unverified paths.
4. **Owner and ticketer, deferred metering polish.** Decide whether to track unmetered-budget visibility and final archive reconciliation after missed polling windows. Keep hosted/in-flight recipient fencing with its existing handoff instead of duplicating it here.

## Decision records

No new ADR proposed or written. The owner’s OpenRouter-first decision and the deliberate hosted fencing boundary are already recorded in the [epic memlog](.memlog.md), [provider story](tickets/story-first-provider-usage-adapter-openrouter.md), and [hosted bridge plan](../epic-one-inbox/tickets/story-plotroom-bridge-for-a-hosted-instance-plan.md).

## Acceptance verdict

**accepted-with-open-items** on the declared evidence. R1–R3 and both local Done-when lines have exact-commit evidence, all seven children are finished, and there is no blocking finding for the exercised local scope. Follow-up dispositions are recorded above.

**Owner disposition, 2026-11-26.** Matt selected “Align dependencies and close #8 (Recommended)” in the Plotroom coordinator chat. The question contrasted aligning native #1 and #2 with the documented story prerequisites and completing after the retrospective lands, or waiting for the full epics. Epic #8 has no canonical whole-epic gates. The initiative’s declarations and all seven stories’ prerequisites remain in place. Only the two native aggregate links are scheduled for removal. Closed #3 remains as a historical native link. The acceptance verdict remains `accepted-with-open-items`. [Native #8](https://github.com/mgd43b/plotroom/issues/8) · [Canonical mapping](epic-guardrails.md#notes) · [Closure rule](../../../../AGENTS.md)

## Open questions

- Should the two verification gaps and bounded metering behaviors be carried as follow-up work? Hosted fencing already has a recorded handoff.

## Assumptions

- The explicit `-H` argument resolves Epic #8 to `epic-guardrails`. Native and canonical status were checked independently. Pending in-epic tickets are empty.
- Exact-run records are the behavior evidence for this headless pass, as requested. “Accepted” is limited to the declared, exercised Guardrails scope, with the deferred caveats recorded above.
- Later merge references replace missing literal backlinks for evidence only. The proposed metadata, test, metering, and dependency actions confer no write or closure authority.
