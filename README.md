# Plotroom

**One shared ledger for every coding agent on your project.**

Plotroom is an MCP server that your Claude Code and Codex sessions all connect to. It records who
owns each piece of work, which files each agent plans to touch, what you decided, and what still
has to happen after a PR merges. A new chat, a compacted chat or a second agent can pick up where
the last one left off by asking the server, not by scrolling back through chat history.

## Why

Run three or four agents at once and the coordination ends up in chat. Who owns this branch? Is
anyone else editing the shared fixture? Did someone check CI on main after that merge? What did
the human actually say? A fresh chat starts blind, and a compacted one forgets. Two agents edit
the same file and find out when the PR conflicts.

Plotroom moves that state out of the conversation and into a ledger every session can read:

- **Lanes with owners.** The orchestrator gives each worker a lane (a branch and its work items)
  with `lane_start`. `snapshot()` recovers the whole board from any chat.
- **File claims that catch collisions early.** Workers `claim` the paths they plan to change, with
  a reason. Overlapping claims, and PRs that touch another lane's claims, become items for the
  orchestrator before the work collides.
- **One attention queue.** `next()` ranks what needs the orchestrator: overlaps, lanes waiting on
  someone, PRs ready to merge, stale questions to the human, actions nobody owns. `wait()` blocks
  until something changes, so agents don't poll.
- **Merge readiness you can check.** `merge_gate(pr)` judges a PR at its exact head: its checks,
  open review threads, main's CI, a stale base and closing keywords. A refusal goes to the lane
  that owns the PR. A person or a clerk session does the merge.
- **Post-merge steps that stay on the board.** `lane_ready` attaches a checklist to a PR and its
  head. After that head merges, the lane stays open until every step is recorded.
- **Briefs written from the ticket.** `brief` drafts a worker's handoff from the ticket's full text
  and the paths other lanes have claimed.
- **Your words, kept exactly.** `decide` records a decision word for word, with where you said it.
  `ask` tracks open questions to you and flags the stale ones. `goal` checks a rule such as "every
  open issue in milestone 1 has an owner" against GitHub.
- **Triggers.** `trigger` fires once, or every time, a condition holds (a PR merges, or main goes
  green with its images built). It can message a worker, notify the orchestrator, mark a lane or
  propose archiving it.
- **Spend caps.** `budget_open` registers a run's cap. Plotroom raises items at 50% and 80% and
  stops the lane at 100%.
- **Search.** `search` covers messages, turn reports, decisions, asks, issue comments and PR bodies,
  with full-text search by default and search by meaning when you configure an embedding endpoint.

Local Claude Code and Codex clients reach Plotroom through `plotroom connect --stdio`. Hosted
instances serve MCP over HTTP. Workers on a Switchboard relay reach it through `plotroom bridge`.

## What it looks like

An orchestrator starts two lanes, and both workers decide to touch the same fixture:

```text
orchestrator  lane_start(lane="api",   worker="agent",  branch="feat/api",   ...)
orchestrator  lane_start(lane="tests", worker="tester", branch="feat/tests", ...)
agent         claim(lane="api",   globs=["src/api/**", "tests/fixtures/user.json"], reason="API change")
tester        claim(lane="tests", globs=["tests/fixtures/*.json"],               reason="test update")

orchestrator  next()
              → { "kind": "claim_overlap", "subject_ref": ["api", "tests"], "state": "open", ... }
```

The orchestrator settles who owns the fixture before either PR exists. If the agent's chat is
compacted or replaced, `snapshot()` still shows its lane, its claims and what it is waiting on.

Every token is bound to one project and one role: `orchestrator`, `clerk`, `worker:<name>`,
`drain` or `bridge`. Each command declares which roles may call it, and anything undeclared is
refused. A worker that tries to start a lane, or to claim paths in another worker's lane, gets
`forbidden`.

## Status

Plotroom is pre-release: run it from a checkout. Nearly all of the planned work is built and
tested. The remaining items are mostly checks against live services and the release itself.

| Works now | Not yet |
|---|---|
| The local daemon, with all of the above over MCP | A tested run against a live GitHub repository ([#29](https://github.com/mgd43b/plotroom/issues/29)) |
| Real Claude Code and Codex clients, locally and against the chart | Releases to PyPI and ghcr.io ([#25](https://github.com/mgd43b/plotroom/issues/25)) |
| Docker Compose, and the Helm chart on a local kind cluster | GitHub App credentials for hosted mode ([#39](https://github.com/mgd43b/plotroom/issues/39)) and a published chart ([#125](https://github.com/mgd43b/plotroom/issues/125)) |
| Restart safety, migration backups, scheduled backups | A provider adapter for real spend readings ([#102](https://github.com/mgd43b/plotroom/issues/102)) |
| The Switchboard bridge | Plotroom coordinating its own development for a working day ([#110](https://github.com/mgd43b/plotroom/issues/110)) |

## Try it in a minute

On macOS or Linux, with Git and uv 0.13.8 or newer. The tests play the orchestrator and the
workers against temporary local daemons, so no credentials, agents or live repository are needed.

```sh
git clone https://github.com/mgd43b/plotroom.git
cd plotroom
uv sync --locked --python 3.14
uv run --locked --python 3.14 python -m pytest -v \
  tests/test_lanes.py::test_local_lane_smoke \
  tests/test_claims_e2e.py::test_conflicting_claim_reported_when_made \
  tests/test_epic6_e2e.py
```

You should see **7 passed**:
- [The lane smoke test](tests/test_lanes.py): a worker marks its lane as waiting, and the
  orchestrator recovers that state after a daemon restart.
- [The claims test](tests/test_claims_e2e.py): an overlap opens, then clears as the workers change
  scope.
- [The brief and checklist tests](tests/test_epic6_e2e.py): a handoff that lists other lanes'
  claims, and a merge that keeps its lane open until the post-merge check is recorded.

## Run your own

Save a config next to the checkout as `plotroom.toml`:

```toml
data_dir = "plotroom-data"

[projects.example.workers.agent]
```

Start the daemon from the checkout, issue a token for each role, and point each MCP client at the
stdio proxy:

```sh
uv run plotroom serve --config ../plotroom.toml
uv run plotroom token issue --config ../plotroom.toml --project example --role orchestrator
uv run plotroom token issue --config ../plotroom.toml --project example --role worker:agent
```

Each client runs `uv run plotroom connect --stdio --config ../plotroom.toml --token-id <id>`, with
the `token_id` issued for its role.

- [Running Plotroom](docs/running.md): the local daemon, Docker Compose, the Helm chart on kind, and
  the Switchboard bridge.
- [Features and configuration](docs/features.md): GitHub reads, the merge gate, briefs, checklists,
  operator goals and search.

## How it's built

One daemon owns a data directory and holds a lock on it. Each project is its own SQLite file,
holding an append-only event log. Commands are checked against the writer-rule table, and every
retried call is idempotent. GitHub is read by polling, so no public webhook endpoint is needed. The
same build runs on a laptop over stdio and hosted in Docker or Kubernetes, with several projects
per instance.

- [Architecture](_bmad-output/initiative-orchestration-control-plane/architecture-orchestration-control-plane/architecture-orchestration-control-plane.md)
- [The plan: initiative and epics](_bmad-output/initiative-orchestration-control-plane/initiative-orchestration-control-plane.md)
- [How this repository coordinates its own development](coordination/README.md)
