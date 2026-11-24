# Plotroom

**Keep coding agents coordinated across chats and worktrees.**

When one agent starts work, another takes over, and you review the PR in a
third chat, the branch survives. The handoff usually lives in chat history.
Plotroom gives your MCP clients a shared project ledger for who owns the work,
which files they plan to touch, what needs your attention, and what still needs
verification after merge.

Say two agents are working on an API change and a test update. In separate
chats, both can decide to edit the same fixture, and the note to verify CI on
main can disappear after the PR merges. With Plotroom, the orchestrator can
see the overlapping claims in `next()` and decide who should own the fixture.
A replacement chat can read `snapshot()` to recover the lane state. The worker
can save "Verify CI on main" in the lane's checklist for a specific PR and head
SHA. When that head merges, the step keeps the lane open until the result is
recorded. To see which issues are still keeping a milestone open, use
[operator goals](#operator-goals).

## How a lane works

A **lane** tracks an assigned worker, a branch, and optional work-item
references. One developer can play the orchestrator role while a coding agent
plays the worker. Add a clerk when you want a separate role to check merge
readiness.

1. The **orchestrator** starts the lane with `lane_start`. If a work-item
   source is configured, `brief` drafts the handoff from the ticket and the
   current path claims.
2. The **worker** records planned paths with `claim` and flags a blocker with
   `lane_update`. The orchestrator uses `next()` for attention items such as
   overlapping claims or a lane waiting on them. `snapshot()` shows the
   current board from a fresh chat.
3. To use a checklist, set up [GitHub reads](#local-github-reads) and wait for
   Plotroom to see the lane's open PR at its current head SHA. The worker or
   orchestrator then calls `lane_ready` with that PR, head SHA, and the
   follow-up steps. Plotroom activates the checklist when it observes that
   exact PR and head merge. The lane's worker or the orchestrator records
   completion with `checklist_done`, and the orchestrator can archive the
   lane once the checklist is complete.

## Try it locally

On macOS or Linux, start from a checkout with Git and uv 0.13.8 or newer.
This tour uses Python 3.14 and starts temporary local daemons. The tests play
the orchestrator and worker roles, use temporary role tokens, and supply
fixture GitHub evidence. You can run it without providing credentials,
starting a coding agent, or connecting to a live repository.

```sh
git clone https://github.com/mgd43b/plotroom.git
cd plotroom
uv sync --locked --python 3.14
uv run --locked --python 3.14 python -m pytest -v \
  tests/test_lanes.py::test_local_lane_smoke \
  tests/test_claims_e2e.py::test_conflicting_claim_reported_when_made \
  tests/test_epic6_e2e.py
```

A successful run reports **7 passed**. The lane smoke test shows a worker
marking a lane as waiting and the orchestrator recovering its state after a
daemon restart. The claims test shows an overlap opening and clearing as
workers change scope. The brief and checklist tests show a handoff with other
lanes' claims and a fixture merge that keeps the lane open until verification
is recorded. The smoke, brief, and checklist tests exercise both HTTP and the
stdio proxy. [Lane smoke test](tests/test_lanes.py) ·
[Claims test](tests/test_claims_e2e.py) ·
[Brief and checklist tests](tests/test_epic6_e2e.py)

## Run your own local instance

Plotroom runs from source as a local daemon with a SQLite ledger per project.
Save this one-worker example as `plotroom.toml` next to the checkout. The
relative `data_dir` keeps the ledger alongside the config, outside the
checkout.

```toml
data_dir = "plotroom-data"

[projects.example.workers.agent]
```

From the checkout, run `uv run plotroom serve --config ../plotroom.toml` to
serve MCP over HTTP. Issue a project-scoped token for each role.

```sh
uv run plotroom token issue --config ../plotroom.toml --project example --role orchestrator
uv run plotroom token issue --config ../plotroom.toml --project example --role worker:agent
```

Configure each MCP client to run
`uv run plotroom connect --stdio --config ../plotroom.toml --token-id <id>` from
the checkout. Replace `<id>` with the `token_id` returned for that client's role.

On macOS and Linux, `plotroom connect --stdio` with a config-derived URL uses a private Unix socket
to verify the local daemon before sending credentials or MCP requests. Restart any `plotroom serve`
process that was already running before upgrading so it can publish this socket. Explicit URLs
continue to use HTTP or HTTPS as configured.

For an explicit HTTPS URL with a bearer token, Plotroom verifies the certificate chain and hostname.
Set `SSL_CERT_FILE` to a PEM CA bundle or `SSL_CERT_DIR` to an OpenSSL hashed CA directory to
configure trust. These bearer connections go directly to the endpoint and bypass environment proxies.

Hosted container and Kubernetes setup are tracked in the project plan.
See the [local server and ledger design](_bmad-output/initiative-orchestration-control-plane/tickets/epic-server-and-ledger/epic-server-and-ledger.md)
and the [architecture notes](_bmad-output/initiative-orchestration-control-plane/architecture-orchestration-control-plane/architecture-orchestration-control-plane.md).

## Local search

Search uses the project's full-text index by default. A project can also configure an
embedding endpoint for semantic search. If the endpoint confirms that one source record
is too large, Plotroom records the rejection for that revision, model, and dimension and
continues indexing later records. The original text stays searchable. A literal match
against a rejected record returns lexical results with `fallback_reason: embedding_rejected`.
Temporary provider failures retry from the last committed checkpoint.

If SQLite reports write contention during full-text backfill or embedding indexing,
Plotroom retries the same checkpointed batch after 100 milliseconds, 500 milliseconds,
and one second. The daemon log reports the project, source, attempt, and failure code
when a write will retry, recovers, or stops. If the retries are exhausted or the write
fails permanently, the affected background input stops. Its next start resumes from
the last committed checkpoint.

## Local GitHub reads

The daemon polls one GitHub repository per configured project. Use the existing
`PLOTROOM_<PROJECT>_GITHUB_TOKEN` environment variable or configure `token_file` in
the GitHub table. The PAT needs read access to Contents, Pull requests, Checks,
Actions, and Issues. `image_jobs` matches workflow job names exactly.

```toml
[projects.example.github]
repository = "owner/repository"
poll_seconds = 60
image_jobs = ["build image"]
```

To mirror work items, select one source in the project table. A local BMad tree
uses `ticket_tree`. A tree pinned to the repository's default branch uses
`github_ticket_tree`, with a repository-relative path. Both take a trusted
`bmad_installation` outside the mirrored tree.

For GitHub Issues, set `bmad_installation` in the project table and add:

```toml
[projects.example.github_issues]
store_config = "path/to/project/_bmad/custom/ticketing-store-config.toml"
# milestone = 1
```

The Issues source layers that file over the trusted installation's BMad GitHub
starter. Without a milestone filter it includes open milestones and standalone
tickets. The daemon preserves the last mirror when a source refresh fails.

## Operator goals

Which open issues still need an owner? Save that rule once, then ask Plotroom
which issues remain and what is missing for each one.

1. Set `human_login` in the project table to use the human's GitHub login.
2. With an orchestrator token, call `goal_set` with a name, goal text, and
   request ID. For example, name the goal below `milestone-1-owners`.
3. Call `goal` with that name to check progress. Omit the name to check all
   stored goals.

```text
every issue where state = open and milestone = 1 satisfies assigned_to human or blocked_by(assigned_to human)
```

Each report lists the remaining issues and points to the clauses that failed
or still need evidence. `as_of` tells you when Plotroom observed the issues.
The example counts only direct, open blockers. If assignment is already
confirmed, the issue passes even when blocker evidence is unavailable.

Predicates are `state = open`, `state = closed`, `milestone = <positive number>`, `label "name"`,
`assigned_to human`, `assigned_to "login"`, `blocked_by(<predicate>)`, `lane "lane-id"`,
and `decision "decision-id"`. Combine predicates with `not`, `and`, `or`, and parentheses.
`lane` tests active lane ownership by GitHub issue reference, and `decision` tests whether
the decision governs the issue. Use `#42` or `owner/repository#42` to link a lane to a GitHub
issue explicitly. A local ticket reference without a known GitHub issue can leave a lane
predicate unavailable.

## Lane briefs

The orchestrator can call `brief(work_item, worker)` for a configured worker.
The tool returns a draft with the full text of the referenced ticket or issue,
the worker's current claims, and the claims of other lanes to avoid. Use the
source's work-item reference. BMad sources use the story file when present and
render the ticket's description, verification, references, and notes when the
ticket lives in `tickets.toml`. Before a worker starts a lane, its paths section
is empty. The orchestrator can edit the returned text before sending it.

The optional project table configures the outline and standing rules. Plotroom
defaults to a Markdown brief with headings and a short plan and RESULT outline.

```toml
[projects.example.brief]
plan = "Scope, implementation steps, and verification."
result = "Status, changes, verification evidence, and any remaining blockers."
standing_rules = ["Use fixture data for acceptance tests."]
```

A custom `template` must use `{source_text}`, `{paths}`, `{dont_touch}`,
`{plan}`, `{result}`, and `{standing_rules}`. It can also use `{worker}` and
`{work_item}`. Write literal braces in the template as `{{` and `}}`.

## Post-merge checklists

Pass `checklist` to `lane_ready` as an ordered array of records with `step`,
`owner`, and `ref`. The named lane snapshot shows the declared version and
the PR and head it belongs to. The checklist becomes active when that exact
PR and head merge.

The owning worker or orchestrator can call `checklist_done` with the lane and
step name. An optional `ref` records completion evidence. If omitted, the
tool uses the declared ref. Open active steps block `lane_archive`. The last
completion opens one archive proposal and emits a `core.checklist_empty` event.
An orchestrator's `checklist_empty` predicate also matches a current active
checklist whose last step has already closed. An explicitly empty checklist
opens an archive proposal as soon as its PR merges.

## Local merge readiness

The orchestrator and clerk can call `merge_gate(pr)` for an advisory verdict
with the checked PR head, the evidence watermark, and the first reason for a
refusal. Configure the gate in the project's GitHub table.

```toml
[projects.example.github]
repository = "owner/repository"
min_checks = 2
ignore_checks = ["Optional review"]
ci_workflow = "ci.yml"
fix_label = "main-fix"
skip_label = "skip-merge"
merge_method = "merge"
controller_merges = false
authorized_label_logins = ["maintainer"]
```

`min_checks` has no default. An ignored check still holds the gate while
pending. `ci_workflow` selects a workflow filename or numeric ID. The optional
fix label waives a red main verdict when a configured GitHub user applied it.
An authorized user can apply `allow-overlap` after Plotroom has observed the
current PR head. The waiver applies to that head occurrence. Controller branch
updates are available through an injected test fixture.

An authorized human can apply `keywords-ok` to waive closing keywords in the
PR body or commit messages. Draft PRs and PRs with the configured skip label
stay out of the live queue. Each lane can set a case-sensitive `branch_pattern`
with `lane_start`. The exact lane branch is the default.

The live controller logs verdict changes and creates a `merge_ready` item
for the clerk, or for the orchestrator when the project has no clerk token.
The item includes SHA pins for a fresh `merge_gate` call and the full command
using the configured merge method. GitHub enforces the PR head pin on the
printed command. Controller-run merges default to off and require an injected
loopback test fixture, where both refs are checked at dispatch. With a live
repository, use the verdict and pinned command for a human or clerk to act on.

The orchestrator can set the deploy window with `freeze(reason, request_id)` and
clear it with `unfreeze(request_id)`. The window is stored in the project ledger
and holds all PRs until it is cleared. `snapshot()` shows the active window.

For flake detection across main commits, a successful check can attach a
notice annotation with the test ID as its title, the source file as its path,
and the exact message `plotroom:test-outcome=passed`. This gives Plotroom
evidence that the previously failing test ran and passed.

## More on the workflow

- [Lane briefs and post-merge checklist design](_bmad-output/initiative-orchestration-control-plane/tickets/epic-briefs-and-checklists/epic-briefs-and-checklists.md)
- [Merge gate rules and evidence](_bmad-output/initiative-orchestration-control-plane/tickets/epic-merge-controller/epic-merge-controller.md)
- [How this repository coordinates its own development](coordination/README.md)
