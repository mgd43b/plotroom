---
id: 2
type: story
title: "Tracer: a merged PR on a test repository reaches the projection once"
parent: epic-one-inbox
covers: [R1, R2]
after: [1.4, 1.5, 1.6]
hitl: true
risk: high
tracker_id: "29"
remote: "https://github.com/mgd43b/plotroom/issues/29"
tracker_status: done
---

# Tracer: a merged PR on a test repository reaches the projection once

## Description

The thinnest path through the GitHub side: a credential provider with a fine-grained PAT (read from the environment of a plotroom serve started by hand, or from a mounted secret file named in config, which is how a daemon that plotroom connect --stdio starts on demand, with a clean environment, gets it), one configured repository per project, a poller built as one fetcher per resource (later entries add fetchers without editing shared code) with its own cursor, PR opened and merged as github.* events with versioned upstream_ids, core's projection of PRs and head SHAs, and a count of open PRs in snapshot(). The owner creates the test repository and one fine-grained PAT scoped to it with contents, pull requests, checks, actions, issues, deployments and workflows (read, plus write for test fixtures), and adds it as a CI secret. Hands on: the credential provider to entries 6, 10, 11 and 16; the poller and projection to entries 14, 3, 4, 15 and 5 and to epics 3, 4, 5 and 7.

## Requirements

Verify: Against the test repository, opening and merging a PR produces each github.* event once, and snapshot() shows the open-PR count rise and fall while the projection shows the PR merged at its head SHA; a daemon started on demand by the stdio proxy polls with the PAT from its secret file. On the same authorized dedicated repository with its scoped PAT, re-verify #32's head changes, close/reopen and restart/cursor continuity. Compare #37's default-branch ticket-tree mirror against a checkout and confirm #52's single PR-merge notification and already-merged creation behavior. For #40, verify lane-report projection and read existing GitHub deployment records on the dedicated test repository. If no suitable deployment record exists, record the missing live sample. Deployment remains deferred. Retain #32's simulated rate-limit check and the original fixture and role checks. Record a public-safe result for each issue.

## References

- parent — _bmad-output/initiative-orchestration-control-plane/tickets/epic-one-inbox/epic-one-inbox.md
- architecture-orchestration-control-plane/architecture-orchestration-control-plane.md#ad-2
- architecture-orchestration-control-plane/architecture-orchestration-control-plane.md#ad-16
- Built: https://github.com/mgd43b/plotroom/pull/145

## Notes

- Check beyond its criteria: a revoked or expired PAT stops polling with a clear error and an unavailable project, and its cursor is intact when a new PAT is configured.
- Hands on from #15 (PRs #50, #92): event kinds and commands register in core/catalog.py build_catalog(); an event's source must equal its kind's prefix; a duplicate returns the stored event whatever its fields, only after role, kind and source pass; migration 0002 carries CHECK(seq > 0) and CHECK(json_valid(fields)), and the next revision's parent is 0002; Ledger.execute checks no role yet. An adapter's cursor write goes through UnitOfWork in the same transaction as its events.
- Hands on: the secret-file reader to epic 8's provider usage adapter (entry 8.3).

### Live fixture acceptance (2026-11-26)

- On Plotroom main `b3bf813c57125ed46b9a280d718951597e4787eb`, the preflight verified that `plotroom connect --stdio` started a disposable daemon and returned a fresh snapshot using the scoped PAT from its configured file. The Checks read returned HTTP 200 on the fixture tip. The live acceptance run on that same revision passed. [Preflight](https://github.com/mgd43b/plotroom/actions/runs/45845547994) · [Acceptance](https://github.com/mgd43b/plotroom/actions/runs/45845942573)
- Fixture PR #1 produced one `github.pr_opened` and one `github.pr_merged` event. The open-PR count went from zero to one and back to zero. The merged projection retained the PR head `6972a064`, distinct from fixture main’s merge `9a6ce8a89a15046c18dbfa5d7d2af88bb0edbc14`. [Fixture PR](https://github.com/mgd43b/plotroom-test/pull/1)
- #32 verified the head change from `f5efe687` to `6972a064`, close and reopen transitions, and restart from the persisted cursor. The simulated rate-limit check passed locally. #37 matched local and remote ready refs at the exact default-branch commits, from `1.1` at `86ecdf31` to `1.1` and `1.2` after merge. #52 verified one notification from the trigger registered before merge and one from the trigger created after the merge was projected. [Acceptance](https://github.com/mgd43b/plotroom/actions/runs/45845942573)
- #40 recorded one owning-lane `deploy_report` and refused the foreign-lane call. The live deployments read returned HTTP 200 with zero records. **The live deployment sample remains pending.** Deployment stays deferred. [Acceptance](https://github.com/mgd43b/plotroom/actions/runs/45845942573)
- The fixture’s naturally triggered opening, update, reopen, and main CI runs passed. #33’s same-head rerun and qualifying flake-history evidence remain separate. [Opening](https://github.com/mgd43b/plotroom-test/actions/runs/45846026975) · [Update](https://github.com/mgd43b/plotroom-test/actions/runs/45846034453) · [Reopen](https://github.com/mgd43b/plotroom-test/actions/runs/45846111035) · [Main](https://github.com/mgd43b/plotroom-test/actions/runs/45846161353)
