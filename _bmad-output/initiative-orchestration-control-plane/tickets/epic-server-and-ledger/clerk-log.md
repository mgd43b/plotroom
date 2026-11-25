# Working-day clerk log

**Dated November 25, 2026** · Evidence for [issue #12](https://github.com/mgd43b/plotroom/issues/12)

## Observation window

The hand-run Codex clerk was observed from its November 22 handoff at 10:28 p.m. through the November 25 evidence freeze at 10:26 a.m. Mountain Standard Time (November 23 05:28:27 UTC through November 25 17:26:07 UTC, end exclusive). The window covers two full weekdays, part of Wednesday, and the Sunday evening handoff. Duty was ongoing and included idle periods and recoveries. This record concerns the hand-run process with a dry-only background sweep and individually authorized live gate calls. The separate Plotroom clerk pilot in [#110](https://github.com/mgd43b/plotroom/issues/110) was outside the observation.

The merge journal was reconciled against GitHub's merged-PR records for `main` by PR, merge SHA prefix, and UTC minute. Trigger counts come from the trigger register and journal. Refusal routing, flake reports, and operational incidents use the retained clerk ledger and tool receipts. The sweep's open-PR file is a current-state snapshot. It supplies no historical denial or delivery count. Counts exclude the outgoing clerk's operation before the handoff.

## Results

| Metric | Observed | Denominator and limits |
| --- | --- | --- |
| Merged PRs | 50 gated merges | 50 GitHub PR merges into `main` in the window. All 50 matched the gate journal. No unmatched PR merge was observed. Direct pushes are outside this comparison. |
| Trigger fires | 50 single fires | 50 eligible PR-specific triggers, one per observed merge. Every recorded fire matched its merge SHA prefix. Standing fallback rules and example templates are outside the denominator. |
| Missed or duplicate fires | 0 missing and 0 duplicate fire markers | 50 eligible PR-specific triggers. This measures the register. Missing or duplicate recipient deliveries were not measured end to end. |
| Trigger lateness | 37 same logged minute, 13 next logged minute, 0 beyond one logged minute | 50 merge-to-fire pairs at one-minute resolution. There was no defined lateness SLA. This measures the fire marker, so exact latency and late receipt are unknown. |
| Trigger delivery exceptions | At least 9 fires needed an alternate delivery route | 50 fires. The original destination had no live listener or the notice was queued for coordinator fallback. A fire marker alone cannot establish receipt. Complete delivery latency is unknown. |
| Refusals routed | At least 18 distinct PR/head/reason cases | 13 unresolved-review-thread cases, three failed-CI cases, one unverified review-evidence case, and one pending-check case. Explicit notices were sent or queued to a lane or coordinator. Repeat sweep sightings and duplicate notices for the same case count once. No complete denominator for all gate attempts or refusals was retained. |
| Flake candidates | 1 reported | One unique candidate in the retained clerk reports. The original failing CI attempt was preserved. A subsequent change addressed the underlying defect. There is no denominator for all failing jobs or confirmed flakes. |
| Coordinator turns saved | Unmeasured | No comparable coordinator-only run and no per-event record of avoided turns. Trigger volume, handoffs, and acknowledgements are observable activity. |
| Clerk token usage and cost | 1,722,452,410 tokens reported by the chat runtime. Billed cost unknown. | Difference between boundary samples of one clerk chat's monotonic cumulative counter, covering 4,500 in-window counter records. The delta was 1,722,168,329 input tokens, including 1,720,557,440 cached input tokens, and 284,081 output tokens. Output includes reported reasoning tokens. Repeated context, polling and bookkeeping are included. The telemetry supplies no attributable invoice or per-task charge. |
| Mistakes and operational problems | 3 documented failure types | Shared relay identity collision, duplicate-process guard false positives, and a premature external-merge classification. The record also shows an unexpected sweep termination followed by recovery. Its cause is unknown. There is no denominator for all clerk actions or all mistakes. |

| Mountain date | Gated PR merges | Trigger fire markers |
| --- | ---: | ---: |
| November 22, evening handoff | 2 | 2 |
| November 23 | 28 | 28 |
| November 24 | 17 | 17 |
| November 25, through the freeze | 3 | 3 |
| **Total** | **50** | **50** |

## Operational observations

- The relay temporarily exposed a shared client identity during recovery. The original identity was restored and the clerk resumed with a separate client. Earlier message receipts could not be reused from the separate client.
- The host-wide duplicate-process check paused renewal when a different repository had a sweep or gate with a matching script name. The clerk verified working-directory ownership before restarting its single dry sweep. An unexpected termination was also recovered after checking for a competing process.
- One dry pass saw a PR disappear before the live gate's journal entry was visible and initially called it external. GitHub and the gate journal confirmed a gated merge. The recorded trigger stayed single.
- In a separate case, evidence of a passing dry gate preceded the authorized merge by about 119 minutes during a coordinator outage. This was a wait for authorization. Its trigger fire marker followed the merge in the next logged minute.

## What the ledger holds

The live ledger is Markdown with a repository heading, operating settings, ticket mappings, an open-PR table, a merged-history table, lanes and routing tables, and free-form decision, verification and incident notes. An open-PR row has PR, lane owner, kind, channel, state and after-merge checklist fields. Merge history has PR, merge commit, method and notes. A lane row has lane, kind, channel and waiting-on fields. The separate trigger register records a condition, routes, actions and fired markers. The merge journal is a row stream of UTC minute, PR number and SHA prefix. The open-PR snapshot and skip list are separate files. The live contents stay private.

## Acceptance disposition

- Decision, 2026-11-25, Plotroom coordinator chat: after reviewing how to handle unmeasurable coordinator turns saved, Matt selected “Accept the log with the gap documented (Recommended)”.
- Coordinator turns saved remain unmeasured. The sampled cumulative chat-runtime delta includes cached input tokens and cannot establish billed cost. The billed amount remains unknown.
- The initiative Notes record the recommendation to retain the current epic order. The separate local ledger-shape re-check belongs to [#195](https://github.com/mgd43b/plotroom/issues/195) and precedes any scheduling of the [#110](https://github.com/mgd43b/plotroom/issues/110) pilot.
