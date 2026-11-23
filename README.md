# plotroom
plotroom

On macOS and Linux, `plotroom connect --stdio` with a config-derived URL uses a private Unix socket
to verify the local daemon before sending credentials or MCP requests. Restart any `plotroom serve`
process that was already running before upgrading so it can publish this socket. Explicit URLs
continue to use HTTP or HTTPS as configured.

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
current PR head. The waiver applies to that head occurrence. Automatic branch
updates run through an injected fixture write port.

An authorized human can apply `keywords-ok` to waive closing keywords in the
PR body or commit messages. Draft PRs and PRs with the configured skip label
stay out of the live queue. Each lane can set a case-sensitive `branch_pattern`
with `lane_start`. The exact lane branch is the default.

The live controller logs verdict changes and creates a `merge_ready` item
for the clerk, or for the orchestrator when the project has no clerk token.
The item includes SHA pins for a fresh `merge_gate` call and the full command
using the configured merge method. Controller-run merges default to off.
The opt-in requires an injected loopback test fixture.

The orchestrator can set the deploy window with `freeze(reason, request_id)` and
clear it with `unfreeze(request_id)`. The window is stored in the project ledger
and holds all PRs until it is cleared. `snapshot()` shows the active window.

For flake detection across main commits, a successful check can attach a
notice annotation with the test ID as its title, the source file as its path,
and the exact message `plotroom:test-outcome=passed`. This gives Plotroom
evidence that the previously failing test ran and passed.
