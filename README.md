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
  with `lane_start`. `snapshot()` summarizes the board and pages through lane details.
- **File claims that catch collisions early.** Workers `claim` the paths they plan to change, with
  a reason. Overlapping claims, and PRs that touch another lane's claims, become items for the
  orchestrator before the work collides.
- **One attention queue.** `next()` pages through ranked items visible to your role, including
  overlaps, lanes waiting on someone, PRs ready to merge, stale questions and missing action routes.
  `wait()` blocks until a visible change or a timeout. On timeout, `data.item` is `null`;
  pass `data.cursor` to the next `wait()` call to keep watching.
- **Merge readiness you can check.** `merge_gate(pr)` returns an advisory verdict for the exact PR
  head using checks, review threads, main's CI, base freshness and closing keywords. The controller
  separately routes actionable refusals to the owning worker or, for an unowned PR, the orchestrator.
  A person or a clerk session does the merge.
- **Post-merge steps that stay on the board.** `lane_ready` attaches a checklist to a PR and its
  head. After that head merges, the lane stays open until every step is recorded.
- **Briefs written from the ticket.** `brief` drafts a worker's handoff from the ticket's full text
  and the paths other lanes have claimed.
- **Your words, kept exactly.** `decide` records a decision word for word, with where you said it.
  `ask` tracks questions tied to an existing GitHub issue comment and flags stale ones. `goal`
  checks a rule such as "every open issue in milestone 1 has an owner" against projected GitHub data.
- **Triggers.** `trigger` can fire once or recur when a condition becomes true, such as a PR
  merging or main going green with its images built. It can message a worker, notify the
  orchestrator, mark a lane or propose archiving it.
- **Budget thresholds.** `budget_open` records a run's cap. With usage meter readings, Plotroom
  raises orchestrator items at 50%, 80%, and 100%. At 100% it queues a stop request for an active
  lane. Delivery depends on its route. Bridged lane-pinned stops surface as failed actions for
  operator attention. OpenRouter is the first live usage meter.
- **Search.** `search` covers messages, turn reports, decisions, asks, issue comments and PR bodies,
  with full-text search by default and search by meaning when you configure an embedding endpoint.

Local Claude Code and Codex clients reach Plotroom through `plotroom connect --stdio`. Hosted
instances serve MCP over HTTP. Workers on a Switchboard relay reach it through `plotroom bridge`.

## What it looks like

An orchestrator starts two lanes, and both workers decide to touch the same fixture.
Use the orchestrator token to start the lanes and each worker's token to claim paths.
[Run your own](#run-your-own) shows how to issue all three tokens.

```text
orchestrator  lane_start(lane="api",   worker="agent",  branch="feat/api",   work_items=[], request_id="r1")
orchestrator  lane_start(lane="tests", worker="tester", branch="feat/tests", work_items=[], request_id="r2")
agent         claim(lane="api",   globs=["src/api/**", "tests/fixtures/user.json"], reason="API change",  request_id="r3")
tester        claim(lane="tests", globs=["tests/fixtures/*.json"],               reason="test update", request_id="r4")

orchestrator  next()
              → { "ok": true, "data": { "items": [
                  { "kind": "claim_overlap", "subject_ref": "[\"api\", \"tests\"]", "state": "open",
                    "context": { "lanes": ["api", "tests"], "claims": [...], ... }, ... } ] } }
```

Reuse the same `request_id` to retry a command for the same role. The orchestrator can settle
who owns the fixture before either PR exists. If a chat is compacted or replaced,
`snapshot(lane="api")` recovers that lane and what it is waiting on, and
`claims(lane="api")` lists its path claims.

Every token is bound to one project and one role: `orchestrator`, `clerk`, `worker:<name>`,
`drain` or `bridge`. Each command declares which roles may call it, and anything undeclared is
refused. A worker that tries to start a lane, or to claim paths in another worker's lane, gets
`forbidden`.

## Status

Plotroom is pre-release: run it from a checkout. Nearly all of the planned work is built and
tested. The remaining items are mostly checks against live services and the release itself.

| Works now | Not yet |
|---|---|
| The local daemon and its MCP tools | A tested run against a live GitHub repository ([#29](https://github.com/mgd43b/plotroom/issues/29)) |
| Real Claude Code and Codex clients against the local daemon and the chart in disposable local kind | Releases to PyPI and ghcr.io ([#25](https://github.com/mgd43b/plotroom/issues/25)) |
| Docker Compose, and the Helm chart on a local kind cluster | Live GitHub App fixture acceptance ([#39](https://github.com/mgd43b/plotroom/issues/39)) and a published chart ([#125](https://github.com/mgd43b/plotroom/issues/125)) |
| Restart safety, migration backups, scheduled backups, and OpenRouter spend readings | Direct Anthropic Console metering |
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

Save this two-worker config next to the checkout as `plotroom.toml`:

```toml
data_dir = "plotroom-data"

[projects.example.workers.agent]
[projects.example.workers.tester]
```

Start the daemon from the checkout. It runs in the foreground:

```sh
uv run --locked plotroom serve --config ../plotroom.toml
```

In a second terminal, from the same checkout, issue a token for each role:

```sh
uv run --locked plotroom token issue --config ../plotroom.toml --project example --role orchestrator
uv run --locked plotroom token issue --config ../plotroom.toml --project example --role worker:agent
uv run --locked plotroom token issue --config ../plotroom.toml --project example --role worker:tester
```

Configure each MCP client to launch the stdio proxy. Set `--directory` to the absolute checkout
path so uv finds the project even when the client starts elsewhere. Set `--config` to the absolute
TOML path and replace `YOUR_TOKEN_ID` with the ID for that client's role.

```sh
uv --directory /path/to/plotroom run --locked plotroom connect --stdio \
  --config /path/to/plotroom.toml --token-id YOUR_TOKEN_ID
```

### Track OpenRouter spend

Add a binding for the exact run ID you will pass to `budget_open`. Use a dedicated inference
key with a positive, non-resetting OpenRouter USD cap and shared OpenRouter billing. Use a
new key per run and keep inference for that run on that key for its lifetime.

```toml
[projects.example.openrouter.trial]
key_file = "/run/secrets/openrouter-trial"
key_sha256 = "<SHA-256 of the trimmed mounted key>"
```

Compute the SHA-256 from the key text after trimming surrounding whitespace, without printing
the key. Keep the digest in private configuration and the key in an owner-controlled mounted
file. Relative paths are based on the configuration file. Keep the binding and key dedicated
to the same run across restarts. A replacement key requires a new run ID. A changed file
fails the configured digest check. Plotroom keeps an opaque reservation in the project ledger
to prevent reassigning an existing budget or reusing its key for a different run in the same
daemon data directory, including inactive projects. Across separate data directories, keep the
key dedicated to its original run.

Open a budget for `run="trial"` with `provider="openrouter"`, the active lane,
a `cap_usd` at least as high as the provider cap, and a `key_scope` of `staging`
or `production`. Budget registration checks the mounted key and provider cap before reserving
the key. The daemon reads lifetime `data.usage` in USD on the project tick.
`budget_status(run="trial")` shows accepted spend and when Plotroom saw it.
Provider accounting may lag. Equal, lower, incomplete, and BYOK readings preserve the previous
spend and as-of. The provider key cap supplies the spend boundary during reporting delay.
After a lane archives, its budgets can reconcile for two minutes. The key remains reserved
when polling stops.
[OpenRouter current-key API](https://openrouter.ai/docs/api/api-reference/api-keys/get-current-key)

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
