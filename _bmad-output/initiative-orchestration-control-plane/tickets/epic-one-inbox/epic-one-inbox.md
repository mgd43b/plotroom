---
tracker_id: "2"
remote: https://github.com/mgd43b/plotroom/issues/2
type: epic
title: "One inbox"
parent: initiative-orchestration-control-plane
covers: [R7, R9, R24, R26]
sources: []
companions:
  - _bmad-output/initiative-orchestration-control-plane/architecture-orchestration-control-plane/architecture-orchestration-control-plane.md
after: []
refined: true
risk: high
---

# One inbox

## Description

GitHub (PRs, checks on main including image builds, review threads, issues and comments), the Codex switchboard (directly or through plotroom bridge), Claude session reports and deploy status arrive as typed, deduplicated events in one inbox, GitHub state is kept in one projection, each event names its lane when one can be inferred, work items are mirrored from a BMad tree on the default branch and from GitHub Issues, and the switchboard is acknowledged upstream once each event commits.

## Outcome

Every signal the orchestrator used to collect from four channels arrives in one place, once, behind one wake signal.

## Requirements

- R1 (parent R7; spine AD-2, AD-16): A GitHub poller turns one repository's PRs, head SHAs, checks per head (main's image-build checks included), workflow runs on main, review threads, changed files, labels with who applied them, issues and comments into typed `github.*` events, each fact once, and core keeps them in one projection.
- R2 (parent R7; decision 2026-11-22): GitHub is reached with a fine-grained PAT in local mode and a GitHub App installation token when hosted, both behind one credential provider; each project names one repository.
- R3 (parent R7, R24; spine AD-10; decision 2026-11-22): plotroom joins the Codex switchboard as an MCP client participant; Codex messages become `switchboard.message` events, and sends go out through the same participant.
- R4 (parent R9; spine AD-10): Each switchboard delivery is acknowledged upstream with its own receipt once its event commits, a duplicate included.
- R5 (parent R24; spine AD-10): `plotroom bridge` runs on the switchboard's machine and carries both directions between the switchboard and a hosted instance with a `bridge` token.
- R6 (parent R26; spine AD-2): `ingest` accepts only `claude.*` events from orchestrator, clerk and worker tokens, a worker's forced to its current lane, and the same turn sent twice is stored once.
- R7 (parent R26): A `SessionReader` port exists, and a time-boxed spike decides whether a local reader implements it; with none, `ingest` alone carries R26.
- R8 (parent R7; spine AD-17; decision 2026-11-22): The `WorkItemSource` mirror can also be supplied from GitHub, read-only: a BMad repo-store tree on the default branch, or a BMad github-store tree of issues; each project's config names exactly one work-item source, so readiness never comes from two places.
- R9 (parent R7; decision 2026-11-22): Deploy status comes from GitHub Deployments and from a deploy lane's own reports, read-only, as typed events.
- R10 (parent R7, R3, R8): An unhandled message from a worker, or a Claude turn report, raises a next item that `ack` closes and that wakes `plotroom watch`.
- R11 (parent R7; spine AD-16): Each inbound event names its lane when one can be inferred: from a PR's branch, or from a switchboard sender's configured channel address.

## Constraints

- From the initiative: C2 (credentials stay outside records), C5 (cursors, keyed actions, at-least-once delivery), C7 (event text is data), C9 (polling, no public webhook), C12 (the switchboard is adapted, not changed).
- From the spine: AD-2, AD-10, AD-12, AD-14, AD-16 and AD-17 bind this epic's stories.

## Done when

- A merged PR, a CI completion and a review thread on a test repository each appear once as typed `github.*` events and in the projection.
- A Codex message arrives through the switchboard, locally and through `plotroom bridge` against a hosted instance, and is acknowledged upstream once it commits.
- A Claude turn report sent through `ingest` twice is stored once.
- The session reader spike's answer is recorded, and R26 is met by `ingest` alone or with the reader.
- A hosted instance polls with a GitHub App installation token across a refresh, and no key or token reaches a record or log.
- GitHub Issues tickets shaped like this repository's and a BMad tree on the default branch both appear in the work-item mirror of the test repository, and nothing is written back.
- A GitHub deployment and a lane's deploy report each appear once as deploy events.
- An inbound message from a worker raises an item that wakes `plotroom watch`.
- A PR event and a switchboard message sent with no lane each land on the lane inferred from the PR's branch and the sender's channel address.

## Boundaries

- Out: reacting to events (triggers in epic 3, the gate in epic 4).
- Owns the GitHub projection (PRs, checks on main including image builds, review threads, issues and comments), the switchboard reader and `plotroom bridge` (both directions), upstream acknowledgment, the roles allowed to call `ingest`, the `SessionReader` port and its spike, the default-branch tree reader, the GitHub Issues work-item reader, lane inference for inbound events, and the deploy status reader.
- Out: provider usage (epic 8).
- Hands on: the projection to epics 3, 4, 5 and 7; switchboard delivery to epic 3.

## References

- parent — _bmad-output/initiative-orchestration-control-plane/initiative-orchestration-control-plane.md, Requirements R7, R9, R24, R26
- architecture — _bmad-output/initiative-orchestration-control-plane/architecture-orchestration-control-plane/architecture-orchestration-control-plane.md

## Notes

- Waits on epic-server-and-ledger story by story, through each entry's `1.<id>` prerequisites, not as a whole.
- Decision: 2026-11-22, refined with the owner: a fine-grained PAT locally and a GitHub App when hosted ("we use PAT for local, for hosted we can use a GH app"); the switchboard reached as an MCP client; deploy status from GitHub Deployments and lane reports; one repository per project.
- Decision: 2026-11-22, the owner chose story-level prerequisites over a whole-epic gate on epic 1 ("Story-level only"), added work items from GitHub Issues ("Add a story", entry 16) and kept the epic whole at 18 entries ("Keep one epic").
- Decision: 2026-11-22, the tracer bullet is entry 2; entry 7 (the switchboard adapter, the least certain) follows it, then the one-session spike (entry 1). The GitHub fetchers run in one chain (14, 3, 4, 15, 5) because they share the projection schema; the switchboard chain (7, 17, 8) runs in parallel with it. A refactor sweep (12) and an end-to-end suite (13) close the epic, as in epic 1.
- Decision: 2026-11-22, `deploy_report` is called by a deploy lane's worker token for its own lane, and its handler appends `deploy.*` as system; a scripted switchboard participant stands in for Codex workers in tests.
- Finding (entry 1, #31, 2026-11-22): a local process can read Claude desktop session turn ends, turn reports and inbound session messages, read-only, with no cooperation from the app, so the `SessionReader` port has a mechanism and entry 18 (#44) stays. Checked on macOS at one app/CLI version, local mode only (cloud and remote sessions were not examined). The probe is `spikes/claude-session-state/probe.py` (standard library only; `survey`, `events`, `watch`; its tests run with `python3 -I -m unittest discover -s spikes/claude-session-state -p "test_*.py"`, outside CI's scope).
- Mechanism, three sources, all internal formats (the Claude Code docs say transcript entries change between versions): (1) the process registry `~/.claude/sessions/<pid>.json`, with `status` busy, idle or waiting and `statusUpdatedAt`, the same data the documented `claude agents --json` prints; a busy to idle flip is the turn end, each seen within about half a second of `statusUpdatedAt` at a 0.5 s poll, in 5 of 5 flips observed in 150 s, with the transcript already grown at that poll. (2) The app's per-session state `~/Library/Application Support/<app>/claude-code-sessions/<account>/<org>/local_<id>.json`, with `cliSessionId`, `completedTurns`, `lastTurnEdgeAt` and `lastTurnReport` (up to 400 characters seen, the latest turn only, replaced by the next turn's). (3) The transcript `~/.claude/projects/<project>/<cliSessionId>.jsonl`, append-only, with every turn's final text and `wrap_up` recap, inbound session messages as user rows whose `origin.kind` is `peer` (with `from`, `name`, `body`), and outbound `SendMessage` tool uses (the probe does not emit those). Join: the registry's `sessionId` is the app state's `cliSessionId` and the transcript's file name; find the transcript by globbing on that id, since the project folder name is internal.
- Turn identity for entries 9 and 18: a turn is a user row carrying `turnPosition.turnIndex` (1-based) up to the next such row; on the 16 live sessions the count of turns equals the app's `completedTurns` counter or exceeds it by one (a turn open or not yet counted), except the longest-lived session, 25 apart over about 2,600 turns, so `completedTurns` is a hint and never the key. Do not key on Stop-hook rows (0 to 4 per turn seen) or `end_turn` rows (0 to 5 per turn seen). The reader's key is (`cliSessionId`, `turnIndex`). A Stop hook's payload has `session_id`, `prompt_id` and `last_assistant_message` but no turn index, so an `ingest()` caller cannot compute that key: entry 9 should accept an optional `turn_index` on `claude.turn_report`, and otherwise dedupe on (session, digest of the report text) inside one turn.
- Limits and rules for entry 18: read-only, never write to the app's files, never connect to the `messagingSocketPath` the registry lists (sending as a session is out of scope), never open the `*.key` files beside the registry. Version-guard every field with a default and degrade to `ingest()` alone when a field is missing. Treat transcript text as data (C7) and keep only the turn report and inbound message bodies, never the rest of a transcript. The supported push alternative is a `Stop` hook (its payload carries `last_assistant_message`; Stop-hook rows appear in desktop-app transcripts, so hooks run there), but installing one edits the user's settings and stays a human step.
