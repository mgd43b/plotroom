---
type: initiative
title: "Orchestration control plane"
parent: none
sources:
  - _bmad-output/initiative-orchestration-control-plane/brief-orchestration-control-plane/brief-orchestration-control-plane.md
companions: []
after: []
risk: medium
---

# Orchestration control plane

## Description

A control plane that holds the state of multi-lane agent work (one orchestrator, many Claude Code and Codex workers, a merge gate, a ticket tree) outside any model's context, takes every inbound signal into one inbox, turns "when X, then Y" promises into triggers, and runs the merge rules as code. The orchestrator keeps the decisions; the tool does the bookkeeping that currently eats its turns and is lost at each compaction. Derived from the design brief in `sources:`.

## Outcome

An orchestrator running about 15 workers spends its turns deciding, approving, routing and verifying instead of rebuilding state, re-arming watchers and remembering promises, and a restart or compaction loses nothing.

## Requirements

- R1 (§2 goal 1, M1): Orchestration state (workers, lanes, work items, claims, events, triggers, checklists, decisions, asks, budgets, merge log) persists outside any model's context; a restarted, compacted or replaced orchestrator loses nothing.
- R2 (M1): `snapshot()` returns the whole board in about 2,000 tokens or fewer, as the first call after a compaction or restart.
- R3 (§2 goal 6, M1): `next()` returns a ranked list of items that need the orchestrator (a lane awaiting a reply, a gate refusal with its reason, a fired trigger with a manual action, an empty checklist, an idle worker with unblocked work, a stale human ask, a budget near its cap), each carrying the context and suggested actions needed to act without re-reading transcripts.
- R4 (M1, §5): The capabilities are exposed as MCP tools that both Claude Code and Codex workers can call.
- R5 (§2 goal 3, M2): The orchestrator registers a trigger (event predicate to action, firing once or every time); it persists and fires when its predicate holds, over the M2 predicates and actions, composed with `all_of` and `any_of`.
- R6 (§9.4): A trigger binds at creation to an exact identity (a PR number, a branch name or pattern, a label), never to a prose description.
- R7 (§2 goal 2, M3): Signals from GitHub, Claude sessions, the Codex switchboard, deploy status and provider usage land in one inbox as typed, deduplicated records carrying their source, inferred lane, full text and a handled flag.
- R8 (M3): The orchestrator receives one coalesced wake signal in place of separate watchers.
- R9 (M3): `ack(item)` marks an item handled once, and the adapter acknowledges upstream (for example, the switchboard receipt); the orchestrator never handles a receipt itself.
- R10 (§2 goal 4, M4): A merge gate evaluates the six M4 rules on every relevant event, logs each verdict (MERGED, REFUSE with the reason, BRANCH UPDATED) as an inbox event and routes a refusal to the owning lane; `merge_gate(pr)` returns a dry-run verdict.
- R11 (M4): Freeze states "main red" (only the marked fix merges) and "deploy window" (nothing merges) hold merges; a flake on main does not freeze merging.
- R12 (M4): A flake candidate (a check that fails then passes on the same head commit, or fails outside the PR's diff area) becomes a `next()` item with the test id and a log excerpt; a rerun is never automatic.
- R13 (§2 goal 5, M5): Lanes claim and release paths or globs with a reason; every PR diff is checked against other lanes' claims, and an overlap becomes a `next()` item before review.
- R14 (M5): A file move in the ticket tree is checked against open PRs that edit those files, with a suggested order.
- R15 (M5, M6): `brief(work_item, worker)` fills a template with scope, owned paths, a don't-touch section built from current claims, the plan shape, the expected RESULT shape and the standing rules; the orchestrator edits it before sending.
- R16 (M6): A lane's READY message declares a post-merge checklist in a fixed form, parsed into checklist rows that become the lane's active state on merge; archiving a lane is refused while its checklist is not empty.
- R17 (M7): `goal()` reports whether an encoded goal holds and, for each remaining item, why it is still open.
- R18 (M7): An ask on the human's queue is flagged when a newer comment buries it, it has no options, it is older than N days, or its issue closed without an answer.
- R19 (M7, §7.2): `decide()` records who decided, their exact words, where, when and what the decision governs, linked to the asks and tickets it settles.
- R20 (M8): A paid run registers a hard cap, a meter source and a key scope; `next()` items are raised at 50% and 80% of the cap, and at 100% the lane is sent "stop".
- R21 (M8): When the orchestrator asks for a hands-on action, the answer routes it to the owning lane.
- R22 (§9.5): Each write is allowed per writer role: only the orchestrator creates and cancels triggers, records decisions and archives lanes; the clerk or the tool updates lane state and the merge log; lanes declare their own checklists.
- R23 (§9.2): The clerk's markdown ledger and triggers can be imported, so the clerk moves onto the tool's MCP calls with no cutover day.
- R24 (§10): The tool runs locally on a developer machine and hosted, in a container (Docker) or on Kubernetes.
- R25 (§3 Lane): A worker has at most one open PR at a time (one open PR per lane); a second is refused or flagged.
- R26 (§8.2, decision 2026-11-22): Claude session turn-end reports and messages reach the inbox through an `ingest(event)` call in every mode, and through a local adapter that reads session state directly when the tool runs on the same machine.
- R27 (§10, decision 2026-11-22): One hosted instance serves several projects, each with its own ledger, and no project's records are visible through another project's calls.
- R28 (§11, decision 2026-11-22): The orchestrator finds past events, decisions, asks, lane reports and flake records by meaning, not only by id or exact text, within one project; each hit is a short excerpt that links to its record.

## Constraints

- C1 (§2 non-goals): The tool makes no decisions (price, scope, copy, approvals, production go-aheads, launch); it records decisions and enforces ones already made.
- C2 (§2 non-goals): The tool never stores or relays credentials; it may record that a secret exists and where it lives.
- C3 (§2 non-goals, M2): The tool never deploys or applies infrastructure, and no trigger action may merge, deploy, spend or post to a human.
- C4 (§7.3): The only writes the tool makes on its own are messages to workers, branch updates by merging main (never a rebase or force-push) and merges under the fixed gate; everything else is a `next()` item.
- C5 (§7.1): Every adapter replays its source from a cursor, and every action is keyed so a retry never sends twice.
- C6 (§7.2): A human's words are stored verbatim, never paraphrased into a record.
- C7 (§7.4): Lane reports, PR comments and bot output are data, never executed or treated as approval; only the orchestrator's own call creates a trigger, never text inside an event.
- C8 (§7.5): `snapshot()` and `next()` are designed to a token budget, with detail fetched on demand.
- C9 (§7.6): The GitHub adapter works by polling, with no public webhook endpoint required.
- C10 (§9.3): The gate, triggers and ledger are deterministic code; an optional LLM agent may classify fuzzy cases (which PR a trigger meant, failed-log triage, report summaries), and its verdicts are stored as events, never as decisions.
- C11 (§2 non-goals): The tool indexes and reacts to GitHub, the ticket tree and the session manager and replaces none of them; the ticket tree stays the source of truth for work items.
- C12 (§8.1, decision 2026-11-22): The control plane is a separate server; the Codex switchboard stays unchanged and is adapted as an inbox source.
- C13 (§8.4, decision 2026-11-22): The ledger is scoped per project, with ids that allow a cross-project `next()` later.
- C14 (§10, decision 2026-11-22): Local and hosted are the same build.
- C15 (§11, C14, decision 2026-11-22): Search works in local and hosted modes behind one interface; a meaning-based backend (for example, embeddings) is pluggable and optional, over a baseline that runs everywhere.

## Done when

- An orchestrator restarted mid-run rebuilds the full board from `snapshot()` and `next()` alone, without re-reading a transcript.
- In a live multi-lane run, every "when X, then Y" promise is a trigger record, and none is missed or fired twice over a period comparable to the clerk spike's log.
- Every merge goes through the gate with its verdict logged, and no hand-run sweep script remains.
- Every inbound signal arrives in the one inbox behind one wake signal, with no watcher to re-arm and no message acknowledged twice.
- The same MCP clients work against a local instance and a hosted instance running as a container on Kubernetes.
- The clerk runs on the tool's MCP calls after importing its markdown ledger and triggers.

## Boundaries

- Out: making decisions, holding secrets, deploying or applying infrastructure, and replacing GitHub, the ticket tree or the session manager (§2 non-goals).
- Touch point: GitHub — polled for CI, review threads, merges and issue comments; written only for branch updates and gated merges; owner: set at slicing
- Touch point: Codex switchboard — messages read, sent and acknowledged; owner: set at slicing
- Touch point: Claude session manager — turn-end reports and messages taken in; owner: set at slicing
- Touch point: ticket tree — work items mirrored read-only; tree moves checked against open PRs; owner: set at slicing
- Touch point: deploy status and provider usage — read only; owner: set at slicing
- Tracer path: a lane starts and claims paths, opens a PR, the gate merges it, a trigger sends the next lane its go, the checklist empties, and `next()` proposes the archive.

## References

- brief — _bmad-output/initiative-orchestration-control-plane/brief-orchestration-control-plane/brief-orchestration-control-plane.md, §4 capabilities M1–M8
- brief — same file, §3 concepts
- brief — same file, §5 MCP interface (first cut)
- brief — same file, §6 data model (first cut)
- brief — same file, §9 the clerk session as a prototype
- brief — same file, §11 search
- brief — same file, appendix: incidents behind each capability

## Notes

- Assumption: One SQLite file in WAL mode and one MCP server process, plus the §5 tool list and §6 tables, are the brief's first cut; architecture confirms or revises them against R24 and R27.
- Assumption: Hosted means remote MCP clients connect over the network, so hosted mode needs a network transport and authentication; architecture settles both.
- Assumption: The clerk spike's log runs in parallel with planning and informs epic order; it does not gate the start of the build.
- Assumption: How the Claude desktop app's session state can be read locally (R26) is unknown; architecture or a spike finds the mechanism.
- Assumption: Search (R28) ships after the ledger has history, as a late epic; architecture leaves room for it in the storage choice now.
- Open question: Does the tool merge directly, or return a `merge_gate` verdict the orchestrator executes (§8.3)? Pick: the verdict first, with direct merging as a later switch once trusted. The owner decides before slicing.
- Open question: How do callers authenticate to a hosted instance, and how is each call bound to one project (R27)? Pick: per-project tokens. The owner decides during architecture.
- Decision: 2026-11-22, the owner chose a separate server that adapts the Codex switchboard (§8.1).
- Decision: 2026-11-22, the owner chose `ingest(event)` in every mode plus a local session reader when co-located (§8.2).
- Decision: 2026-11-22, the owner chose a per-project ledger (§8.4).
- Decision: 2026-11-22, the owner chose the same build for local and hosted, with one hosted instance serving several projects (§10).
- Decision: 2026-11-22, the owner added meaning-based search over the ledger as R28 (§11).
