# Features and configuration

How to configure GitHub reads, the merge gate, lane briefs, post-merge checklists, operator goals and search. Back to the [README](../README.md).

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

### GitHub App credentials

For a project using an installed GitHub App, add its public client ID and
installation ID and point to its mounted private-key file. For example:

```toml
[projects.example.github]
repository = "owner/repository"

[projects.example.github.app]
client_id = "Iv1.example"
installation_id = 123456
private_key_file = "secrets/github-app.pem"
```

The path is relative to the config file. App mode uses the mounted key and
keeps installation tokens in memory until refresh. App authentication uses
the GitHub HTTPS API. Projects without this
table use the PAT configuration above. The App needs the repository selected
in its installation and read access to Actions, Checks, Contents, Deployments,
Issues, and Pull requests. Plotroom requests those permissions for the configured
repository only. Keep private-key bytes out of TOML and the project data directory.

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

Projects can require completed Kodus review before a MERGE verdict. Configure
the numeric GitHub App ID, App slug, and exact check name in the trusted
project configuration.

```toml
[projects.example.github.kodus_review]
app_id = 123456
app_slug = "kodus-example"
check_name = "Kody Code Review"
```

Plotroom requires the App's completed review result for the exact PR head,
with evidence linking the check to that PR. For a successful check with an
empty PR association list, a submitted review by the App's GitHub bot can
provide corroboration when it names the exact commit and falls within the
check's run. A success with neither a direct association nor a qualifying
submitted review leaves the gate closed, including when there are no findings.
A cadence skip requires a direct GitHub association to the PR.

Project administrators can attest the opening head if they independently
witnessed it when the PR opened. Record the repository, PR number, immutable
numeric GitHub PR ID, creation time, and full opening SHA.

```toml
[[projects.example.github.kodus_review.opening_heads]]
repository = "owner/repository"
pull_number = 42
pull_id = 123456789
created_at = "2026-11-25T20:00:00Z"
head_sha = "1234567890abcdef1234567890abcdef12345678"
```

Cadence also requires a completed review of that opening head and a designated
skip from the trusted App on the current head. The opening review must finish
by the time the skip starts. Resolved threads need a reply
from the PR author or an owner, member, or collaborator who is a human and
different from the thread author. Plotroom reads the complete thread list and
up to 100 comments per thread. Incomplete evidence keeps the gate closed.
Without an attestation, a completed review on the current head is required.
Automatic capture of the opening head is separate from this policy.

The orchestrator can set the deploy window with `freeze(reason, request_id)` and
clear it with `unfreeze(request_id)`. The window is stored in the project ledger
and holds all PRs until it is cleared. `snapshot()` shows the active window.

For flake detection across main commits, a successful check can attach a
notice annotation with the test ID as its title, the source file as its path,
and the exact message `plotroom:test-outcome=passed`. This gives Plotroom
evidence that the previously failing test ran and passed.

A trusted GitHub Actions workflow in the configured repository can also publish
one immutable artifact per job attempt with just `junit.xml` at the ZIP root.
Name it `plotroom-junit-<run-id>-<attempt>-<job-name-hash>`, using
`github.run_id`, `github.run_attempt` and the lowercase SHA-256 of the UTF-8
job display name as GitHub lists it. Give matrix jobs distinct display names.
Reserve each name for its own job and upload without overwriting artifacts.
Plotroom verifies GitHub's run and job linkage and the archive digest. The
workflow is responsible for ensuring that the named artifact came from that job.

Use a UTF-8 report with repository-relative `file`, `classname` and `name`
on every testcase. For pytest, use `--junitxml=junit.xml -o junit_family=xunit1`
to include the file. Plotroom uses `classname::name` as the test ID. Use the
same path and ID in annotations to match evidence from the two sources.
The report limits are 1 MiB compressed, 4 MiB uncompressed and 5,000 testcases.
At most 300 testcases may have failures or errors, matching the limit for
structured check annotations.
See GitHub's [workflow jobs](https://docs.github.com/en/rest/actions/workflow-jobs)
and [workflow artifacts](https://docs.github.com/en/rest/actions/artifacts) APIs.

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

A checklist needs [GitHub reads](#local-github-reads): Plotroom must see the lane's open PR at its
current head SHA before `lane_ready` can attach one.

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

## Operator goals

Which open issues still need an owner? Save that rule once, then ask Plotroom
which issues remain and what is missing for each one.

Goal evaluation needs an available GitHub issue projection. Configure
[GitHub reads](#local-github-reads) for the project before evaluating a goal.

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

## Local search

Search uses the project's full-text index by default. A project can also configure an
embedding endpoint for semantic search. To confirm that one input is too large, the endpoint must
return HTTP 413 for a single input, with no `Retry-After` header and `error.code` set to
`input_too_large` in a JSON response of at most 4 KiB. Plotroom records the rejection for
that revision, model, and dimension and continues indexing later records. A literal match
against a rejected record returns lexical results with `fallback_reason: embedding_rejected`.
Unconfirmed rejections and temporary provider failures retry from the last committed checkpoint.

If SQLite reports write contention or a full database during full-text backfill or
embedding indexing, Plotroom retries the same checkpointed batch after 100 milliseconds,
500 milliseconds, and one second. The daemon log reports the project, source, attempt,
and failure code when a write will retry, recovers, or stops. If the retries are exhausted
or the write fails permanently, the affected background input stops. Its next start resumes
from the last committed checkpoint.

## More on the workflow

- [Lane briefs and post-merge checklist design](../_bmad-output/initiative-orchestration-control-plane/tickets/epic-briefs-and-checklists/epic-briefs-and-checklists.md)
- [Merge gate rules and evidence](../_bmad-output/initiative-orchestration-control-plane/tickets/epic-merge-controller/epic-merge-controller.md)
- [How this repository coordinates its own development](../coordination/README.md)
