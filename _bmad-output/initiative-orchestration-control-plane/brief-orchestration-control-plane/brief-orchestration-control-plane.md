---
type: brief
title: Orchestration control plane for multi-lane agent work
date: 2026-11-22
---

# Design brief: an orchestration control plane for multi-lane agent work

This is the public, sanitized copy of a design brief written 2026-11-22 by an orchestrator session on a real multi-agent project. Project-specific names, repositories and incident quotes have been replaced with generic examples. The motivating setup: one Claude orchestrator, about 10 Claude Code lanes and 5 Codex workers, a merge sweep, two clusters (staging and production), and a BMad ticket tree. The tool should be general.

## 1. Problem

The orchestrator's job is to decide, approve, route and verify. With about 15 workers, most of its turns go to bookkeeping instead:

- **State lives in the orchestrator's context, and compaction loses it.** After each compaction the orchestrator rebuilds from a lossy summary. It then forgets who owns what, which decisions are made and which handoffs are due. This is where role drift came from: hands-on infrastructure work done by the orchestrator right after a compaction.
- **Inbound signals arrive on four channels**, each read differently:
  - lane turn-end notes, cut to 400 characters, so the full text needs extra calls;
  - Claude session messages;
  - the Codex message bus (a "switchboard"), which needs a polling watcher re-armed after every message (missing one re-arm left the Codex workers idle);
  - GitHub CI, review-thread and merge state, polled by a scratchpad script.
- **"When X, then Y" promises live only in memory.** Examples:
  - "when #101 merges, tell the reader lane to start V1";
  - "when #102 merges, deploy with --latest";
  - "when #103 merges, archive the lane";
  - "when the confirmation run finishes, start the answer half".
  Each is a chance to forget. Forgotten archiving left the human archiving sessions by hand.
- **The merge rules are a 29-line bash script in a scratchpad.** It runs on bash 3.2, dies with the session, and keeps no history.
- **File ownership is enforced by prose in briefs.** Conflicts are found by hand, for example a tree pass moving story files that five open PRs edit.
- **Post-merge follow-through is a markdown file.** "A merged PR is not a finished task", but nothing tracks the list of steps after a merge.

## 2. Goals and non-goals

Goals:
1. **Durable state outside any model's context.** The orchestrator can be restarted, compacted or replaced and lose nothing.
2. **One inbox** for every signal, typed, deduplicated, and acknowledged once.
3. **Triggers that fire on events.** Promises become records, not memory.
4. **Merge gating as a service**, with the rules as code and a log.
5. **File claims and overlap detection** between lanes.
6. **A single call, `next()`, that tells the orchestrator what needs a decision now.**

Non-goals:
- **No decisions.** Price, scope, copy, approvals, production go-aheads and launch stay with the humans and the orchestrator. The tool records decisions (with the human's exact words) and enforces the ones already made.
- **No secrets.** It never stores or relays credentials. It may record that a secret exists and where it lives.
- **No hands-on actions on infrastructure.** It doesn't deploy or apply. Deploy lanes do, and the tool tracks their state.
- **Not a replacement for GitHub, the ticket tree or the session manager.** It indexes and reacts to them.

## 3. Concepts

| Concept | What it is |
|---|---|
| **Worker** | A Claude Code session or a Codex worker. It has an id, a kind (claude or codex), a model, a channel (session messaging or switchboard), and a state. |
| **Lane** | A worker bound to one task: ticket ids, branch, PR, owned paths, state, and what it's waiting on. A worker has at most one open PR ("one open PR per lane"). |
| **Work item** | A ticket or issue from the tracker or tree: id, epic, status, blocked-by, labels, and the owning lane if any. |
| **Claim** | A lane's claim on file paths or globs, with a reason. Claims are visible to every brief and checked against every PR's diff. |
| **Trigger** | `on(event predicate) -> action`, created by the orchestrator or a lane, persisted, firing once or every time. |
| **Checklist** | A lane's post-merge steps (staging check, posting, issue comment, ticket, archive). The lane is archivable when its checklist is empty. |
| **Ask** | An item on the human's queue: issue, exact ask, the options, the date it was posted, and whether it's still the latest comment. |
| **Decision** | A recorded decision: who made it, their exact words, where, when, and what it governs. |
| **Budget** | A paid run's hard cap, provider, key scope (staging or production), meter source and current spend. |

## 4. Capabilities, in build order

### M1. Ledger and `next()` (the biggest payoff)
- A SQLite store, one file, WAL mode, with the tables in section 6.
- Exposed as MCP tools (section 5), so Claude and Codex both use it.
- `next()` returns a ranked list of items that need the orchestrator: a lane waiting on a reply, a READY-TO-MERGE lane the gate refused (with the reason), a trigger that fired with a manual action, an empty checklist (archive it), an idle worker with unblocked work, a stale human ask, or a budget near its cap. Each item carries the context needed to act on it, so the orchestrator doesn't re-read transcripts.
- `snapshot()` returns the whole board in under about 2,000 tokens. It's the first call after any compaction or restart.

### M2. Triggers
- Event predicates: `pr_merged(n)`, `pr_green(n)`, `pr_threads_resolved(n)`, `main_green_with_images(sha?)`, `run_finished(id)`, `lane_idle(id)`, `checklist_empty(lane)`, `message_from(worker)`, `deploy_finished(env, sha)`, `issue_closed(n)`, `budget_ratio(run) >= x`, and `all_of(...)` / `any_of(...)`.
- Actions:
  - `send(worker, text)` delivers over the worker's channel;
  - `notify_orchestrator(text)` puts the item into `next()`;
  - `mark(lane, state)`;
  - `propose_archive(lane)`.
- No action may merge, deploy, spend or post to a human. Those stay explicit calls by the orchestrator, or by the merge controller under its fixed rules.
- Example: `on(all_of(pr_merged(104), pr_merged(flakefix))) -> send(reader-lane, "both merged; merge main, rerun")`.

### M3. Unified inbox
- **Adapters:**
  - GitHub, through webhooks or a poll of `gh api` events: CI completion, review threads, merges, issue comments;
  - Claude sessions: turn-end reports in full, plus session messages;
  - the Codex switchboard: messages, acked on the worker's behalf once the orchestrator handles them;
  - deploy status, read-only;
  - provider usage, for budgets.
- **Records:** every event becomes a typed record with a source, a lane (when one can be inferred from the branch or sender), its full text and a handled flag.
- **One wake signal:** a single notification to the orchestrator, coalesced, replaces separate watchers.
- **Acknowledgment:** the orchestrator handles an item, then calls `ack(item)` once. The adapter acknowledges upstream (for example, the switchboard receipt). The orchestrator never handles a receipt itself.

### M4. Merge controller
- The current gate as code, run on every relevant event instead of every 2 minutes:
  1. every check finished, none failed (with a named exception list, for example a review bot that's down);
  2. zero unresolved review threads;
  3. main's latest completed CI run is green, or the PR is marked as the fix for a red main;
  4. no file the PR changes has also changed on main since the PR's merge base (the stale-CI rule). If one has, update the branch, without rebasing, and wait for the new run;
  5. no closing keyword that would close an issue before it's verified on staging;
  6. merge pinned to the head commit.
- Logs each decision (MERGED, REFUSE with the reason, BRANCH UPDATED) as an inbox event, and routes a refusal to the owning lane.
- **Freeze states:** "main red" (only the marked fix merges) and "deploy window" (nothing merges). A flake on main doesn't freeze: the controller files it as a flake item and keeps merging.
- **Flake detection:** a check that fails and then passes on the same head commit, or fails on a PR whose diff doesn't touch the failing test's area, is a flake candidate. It goes to `next()` with the test id and log excerpt. A rerun-and-move-on is never automatic.

### M5. Claims and overlap
- `claim(lane, paths, reason)` and `release`. Every PR diff is checked against other lanes' claims, and an overlap becomes a `next()` item before review time.
- Tree moves (file renames in the ticket tree) are checked against open PRs that edit those files, with a suggested order.
- Briefs get an automatic "don't touch" section built from current claims.

### M6. Briefs and checklists
- `brief(work_item, worker)` fills a template:
  - scope, owned paths, claims to avoid, and the plan shape (for example BMad: plan, code, review, verify);
  - the expected RESULT shape;
  - the standing rules (no attribution, no force-push, targeted tests, the human's spelling and language rules).
  The orchestrator edits it, then sends it.
- A lane's READY message declares its post-merge checklist in a fixed form. The tool parses it into checklist rows. On merge, the checklist becomes the lane's active state.

### M7. The human's queue and the goal check
- **Goal checks:** encodable goals, for example "every open issue is assigned to the human or blocked by one that is". `goal()` reports what's left and why each item is still open.
- **Ask checks:** each ask assigned to the human must be one short, standalone, latest comment. The tool flags a newer comment burying it, an ask with no options, an ask older than N days, and an ask whose issue closed without it being answered.
- **Decision records:** the human's exact words, linked to the asks and tickets they settle.

### M8. Guardrails
- **Budgets:** a paid run registers a hard cap, a meter source and a key scope. The tool raises `next()` items at 50% and 80%, and at 100% sends "stop" to the lane.
- **Role guard:** the orchestrator's tool surface has no kubectl, push or deploy. When the orchestrator asks the ledger for something hands-on, the answer is "route it to <lane>". This prevents the role drift described in section 1.

## 5. MCP interface (first cut)

```
snapshot() -> board summary
next(limit=10) -> [item]                 # ranked, each with context and suggested actions
ack(item_id, outcome)                     # handled; acks upstream where needed

lane_start(worker, work_items, branch?, claims?)  # record a new lane
lane_update(lane, state?, pr?, waiting_on?, note?)
lane_ready(lane, pr, head_sha, checklist[])
lane_archive(lane)                        # refused while the checklist isn't empty

claim(lane, paths[], reason) / release(lane, paths[])
overlaps(pr | lane) -> [conflict]

trigger(on, action, once=true, note) -> id
triggers(lane?) -> [trigger]   /  cancel(trigger_id)

merge_gate(pr) -> {verdict, reasons[]}    # dry run of the controller
freeze(kind, reason) / unfreeze(kind)

decide(who, exact_words, where, governs[]) -> decision_id
ask(issue, text, options[]) / asks(stale_only?)
goal() -> {done: bool, remaining: [item with reason]}

budget_open(run, cap_usd, provider, key_scope) / budget_status(run?)
brief(work_item, worker) -> text
```

## 6. Data model (SQLite)

- `workers(id, kind, model, channel, channel_address, state, last_seen)`
- `lanes(id, worker_id, title, branch, pr, state, waiting_on, created, archived)`
- `lane_items(lane_id, work_item_id)`
- `work_items(id, tracker_ref, tree_ref, epic, status, blocked_by_json, labels_json)`, mirrored from the tracker; the tree stays the source of truth
- `claims(lane_id, glob, reason, created)`
- `events(id, source, kind, lane_id, ref, body, created, handled, handled_outcome)`
- `triggers(id, predicate_json, action_json, once, created_by, fired_at, cancelled)`
- `checklist(lane_id, step, owner, state, ref)`
- `decisions(id, who, exact_words, where_ref, governs_json, created)`
- `asks(issue, text, options_json, posted_at, comment_id, latest, answered_by_decision)`
- `budgets(run, cap_usd, provider, key_scope, spent_usd, updated)`
- `merge_log(pr, head_sha, verdict, reasons_json, at)`

## 7. Design rules

1. **Restartable and idempotent.** Every adapter can replay its source from a cursor. Every action is keyed so a retry doesn't send twice.
2. **Exact words.** A decision stores the human's words verbatim. The tool never paraphrases a human into a record.
3. **Read-mostly integrations.** The only writes the tool makes on its own are messages to workers, branch updates (merging main, never a rebase or force-push) and merges under the fixed gate. Everything else is a `next()` item.
4. **Untrusted text stays data.** Lane reports, PR comments and bot output are stored and shown, never executed or treated as approval. A trigger can't be created from text inside an event. Only the orchestrator's own call creates one.
5. **Small context cost.** `snapshot()` and `next()` are designed to a token budget. Detail is fetched on demand.
6. **Local first.** One SQLite file and one MCP server process. The GitHub adapter can poll (no public webhook endpoint needed).

## 8. Open questions for the builder

1. **Integration:** should it extend the Codex switchboard (which already has durable tasks and a message bus), or be a separate server that adapts it? Extending saves an adapter; separating keeps the switchboard minimal.
2. **Claude sessions:** how does the tool read Claude Desktop session state and turn-end reports outside the orchestrator's tool calls? If the session manager has no API for this, the orchestrator forwards them (one `ingest(event)` call per notice), which is still far cheaper than holding state in context.
3. **Who runs merges:** the tool directly, or a `merge_gate` verdict that the orchestrator executes? The second is simpler to trust. The first removes the sweep entirely.
4. **Multiple orchestrators:** is the ledger per project or per human? Per project is simpler. Per human allows a cross-project `next()`.

## 9. The clerk session as a prototype (added 2026-11-22)

On 2026-11-22 the human started a "clerk" session: a second, cheaper Claude session that runs the merge sweep, fires "when X merges, tell Y" triggers from a markdown file, and keeps a markdown ledger. It makes no decisions. It's a hand-run version of M1 to M4, and it changes this brief in five ways:

1. **Treat the clerk as the spike.** Before building, log what it does for a few days:
   - triggers fired, missed or fired late;
   - gate refusals it routed;
   - flake candidates it found;
   - the orchestrator turns it saved, and its own token cost;
   - any mistake it made.
   That log is the evidence for which milestone pays first. The orchestrator's prediction is M2 (triggers) and M1 (the ledger).
2. **The schema already exists.** The clerk's `ledger.md` and `triggers.md` are the data model in markdown. The tool's first version can import them, and the clerk can then move onto the tool's MCP calls without a cutover day.
3. **Decide which parts are code and which are an agent.** The gate, the triggers and the ledger are deterministic and belong in code. Some parts are fuzzy and need judgment:
   - recognizing which new PR a trigger meant;
   - reading a failed log and deciding whether the failing test is outside the PR's diff;
   - summarizing a lane's report.
   Those are the parts an LLM does well. So the design is a deterministic server plus an optional agent for classification. Its verdicts are stored as events, never as decisions.
4. **Triggers need identity, not prose.** The clerk's first trigger says "a PR from the orchestrator's subagent that fixes the flaky embed test": a description, matched by judgment. The tool should bind a trigger to something exact at creation: a PR number, a branch name or a label. A PR that doesn't exist yet gets a branch-name pattern.
5. **Several writers need permissions per field.** With an orchestrator and a clerk (and, later, lanes) writing the same state, the data model needs a writer rule per table:
   - only the orchestrator creates and cancels triggers, records decisions and archives lanes;
   - the clerk or the tool updates lane state and the merge log;
   - lanes declare their own checklists.
   Add this as a `writer` column, or as a check per tool call.

What the clerk doesn't change:
- **Durable state still matters.** The clerk also compacts, so its ledger file is what survives, and the tool's SQLite replaces that file.
- **Non-goals hold:** no decisions, no secrets, no deploys.
- **One inbox is still needed.** The clerk adds a fifth message hop (clerk to orchestrator), which is the cost M3 removes.

## 10. Deployment targets (added 2026-11-22)

The project owner, 2026-11-22, verbatim: "It should be able to run locally, or in a hosted (thing docker/k8s) location".

## Appendix: incidents behind each capability (generalized)

| Incident | Capability |
|---|---|
| After a compaction, the orchestrator made production quota, egress and secret changes itself, and the human had to remind it that its role is orchestration, not engineering. | M1 snapshot, M8 role guard |
| The human observed the orchestrator losing focus after each successive compaction and asked whether a goal should be set. | M1, M7 goal check |
| The switchboard watcher wasn't re-armed, and Codex workers sat idle. | M3 one inbox |
| Switchboard messages were answered but not acknowledged, then redelivered. | M3 ack once |
| The orchestrator paused all merges for a flake already on main, and the human, tired of waiting, merged PRs by hand. | M4 flake rule, no freeze for flakes |
| The human archived finished lanes by hand because the orchestrator wasn't doing it. | M6 checklists, M2 `checklist_empty` trigger |
| Lanes waited on "message me when #N merges" promises, and the CI lane noted that an orchestrator message is its only wake signal for a merge. | M2 triggers |
| A tree pass moved story files that five open PRs edit. | M5 tree-move overlap |
| A 1,600-word issue thread buried the human's ask. | M7 ask checks |
| A burst of main merges superseded image builds, so staging had no deployable commit. | M2 `main_green_with_images` |
