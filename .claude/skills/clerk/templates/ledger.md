# Clerk ledger: <owner>/<repo>

The clerk keeps this file current. The coordinator reads it first after a compaction or a restart. Times are UTC. Last full update: <YYYY-MM-DD HH:MMZ> (<who>).

Coordinator session: `<COORDINATOR_ID>` ("<its title>"). Human: <name or handle>.

This file is per-run state with session ids. It lives in the state folder (`ORCH_STATE_DIR`), never in git.

A **channel** says how to reach a worker: `session-message` (a Claude session, by its id), `relay:<name>` (a message relay both Claude and Codex sessions can call), `coordinator-only` (the clerk never messages it; tell the coordinator, who relays), or `human`.

## Open PRs

| PR | Owner (lane) | Kind | Channel | State | After merge |
|---|---|---|---|---|---|
| #<N> | <lane name> | claude \| codex \| other | `<channel>` | <what it is waiting on: CI running, fixing a thread, holding for X> | <Trigger T<n>, or "ask the lane for its post-merge list"> |
| #<N> | <coordinator's subagent> | subagent | coordinator-only | <state> | Tell the coordinator |
| (soon) | <owner> | <kind> | <channel> | <being written> | <trigger> |

## Lanes with no open PR

| Lane | Kind | Channel | Waiting on |
|---|---|---|---|
| <lane name> | <kind> | `<channel>` | <what, and who decides> |

## Skip list (mirrors `skip.txt`)

| PR | Why, and who asked |
|---|---|
| #<N> | <the lane wants it left alone / a hold the coordinator set> |

## Merges the sweep saw outside the gate

| PR | Merge | Noted | What the coordinator needs to do |
|---|---|---|---|
| #<N> | <sha8> | <time> | <fire the PR's trigger; update this ledger> |

## Flake candidates already sent (once per test)

| Test id | Run id | Sent |
|---|---|---|
| <test id> | <run id> | <time> |

## Log

- <YYYY-MM-DD HH:MMZ>: #<N> merged at <sha8>. <Trigger fired, or none.>
