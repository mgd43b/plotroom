# Coordinating plotroom's own development

Until plotroom can run its own coordination, multi-session work on this repository uses the
hand-run coordinator, clerk, ticketer and task-coordinator skills. They are copied unchanged from
`mgd43b/prompts` at commit `b69aa95` into `.claude/skills/` (Codex reaches the same folders through
the links in `.agents/skills/`). Update them by copying the folders again from a newer commit and
changing the commit here; edit a copy only when a rule really differs for this project.

## The roles here

| Role | Skill | Goal to set (`/goal`, or paste as the first message) |
|---|---|---|
| Coordinator | `coordinator` | [goals/coordinator.txt](goals/coordinator.txt) |
| Clerk | `clerk` | [goals/clerk.txt](goals/clerk.txt) |
| Ticketer | `ticketer`, with BMad's `bmad-agent-po` (Sofia) for the mechanics | [goals/ticketer.txt](goals/ticketer.txt) |
| One task only | `task-coordinator` | [goals/task-coordinator.txt](goals/task-coordinator.txt) |

The goals are the prompts repository's goals with this project's names filled in. Session ids
(`<COORDINATOR_ID>`, `<CLERK_ID>`, `<TICKETER_ID>`) are filled in when the sessions exist, and
never committed.

## Settings for the clerk's scripts

Run the scripts from the repository root (or any worktree of it), so `.claude/skills/...` resolves.

| Setting | Value |
|---|---|
| `ORCH_STATE_DIR` | default, `~/.local/state/agent-orchestration/mgd43b-plotroom` (outside git) |
| `ORCH_MIN_CHECKS` | `2`: the `check (python 3.14)` and `check (python 3.15)` jobs of `ci.yml`, counted from `gh pr checks` on PR #28 |
| `ORCH_CI_WORKFLOW` | `ci.yml` |
| `ORCH_MERGE_METHOD` | `merge` (the default) |

## Bootstrap order

1. **No CI yet.** The gate refuses any PR whose base branch has no readable CI verdict, so it can't merge
   anything until CI exists. The tracer story (#13) adds CI; the coordinator reviews its PR and the
   human merges it by hand. From then on every merge goes through the gate.
2. Set `ORCH_MIN_CHECKS` and `ORCH_CI_WORKFLOW` above, then start the clerk.
3. The ticket tree is BMad's, under `_bmad-output/initiative-orchestration-control-plane/`, mirrored to
   GitHub Issues on this repository (milestone 1). Sofia (`bmad-agent-po`) publishes; the ticketer
   skill supplies the discipline around her (write-lists, the human's go-ahead, sequencing with open
   PRs).

## When this bootstrap ends

plotroom replaces these files piece by piece:

- **Epic 1 (#1):** the ledger and `next()` replace the clerk's `ledger.md`.
- **Epic 3 (#3):** triggers replace `triggers.md`.
- **Epic 4 (#4):** the merge controller replaces `safemerge.sh` and `sweep.sh`.
- **Epic 9 (#9):** the clerk imports its ledger and triggers, then runs on plotroom's tools.

After epic 9, the clerk skill here is retired and the coordinator talks to a running plotroom.
