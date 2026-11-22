# Epics and labels

## Epic size

An epic is a theme a person can hold in their head, and a tracker object with limits.

- **Rule of thumb: split an epic once it passes about twelve tickets**, or when it holds more than one area with different owners. Small epics read faster, finish faster and give the coordinator a clear "this epic is done" moment (and a retrospective).
- **Hard limits exist.** GitHub allows at most **100 sub-issues per parent**; other trackers have their own. Check an epic's count before a large publish (`gh issue view <epic> --json subIssuesSummary`), and **offer the split once it passes 80**.
- **If a create or relation fails at the cap** ("cannot have more than 100 sub-issues"): don't retry, and don't look for another parent to hang it on. The epic is too large. Leave the new issue where it is (in the milestone, with its blocked-by links), record the failure in the epic's Notes, and tell the human and the coordinator: it needs a re-slice.
- One run hit that cap, and the human's reaction became the rule: "i mean 100 issues is a little larger for a single epic :)".

## Splitting (re-slicing) an epic

A split changes the tree's structure, so it is its own tree pass, with the human's decision and their go-ahead.

1. **Propose the split, don't do it.** By area or owner, not by arrival order: name the new epics (a one-line theme each), say which entries move where, and say which stay as history (finished entries can stay in the old epic as the record). Show a table: old ref, new ref, title, status.
2. **Check the open PRs** that edit the entries or files you would move, and sequence the move after them ([tree-pass.md](tree-pass.md#sequencing-with-open-prs)).
3. **Get the decision**, with the human's exact words, and record it in the old epic's Notes ([decisions.md](decisions.md)): what was split, why, and the old and new refs.
4. **Move the entries.** Renumber consistently, rewrite every dependency that names a moved entry (a mover tool does this where the project has one), and search the prose for references by old number ("see entry 4.2") — tools rarely rewrite those; do it by hand and list what you changed.
5. **Move the tracker side after the tree**, in the write-list: new epics, new parents for the moved issues, labels. Never delete an issue to "move" it; re-parent it.
6. Re-check the counts and the dependency order, and put the moved entries in build order by hand.

## Label sync

Labels carry type, status and the dimensions the project filters by (area, kind, size, readiness, gate). Some labels are written by a publish (type, status, risk); others the tree computes or holds in a sidecar. The rules:

- **Know which labels you manage.** Write the list down (by exact name or prefix: `area:`, `kind:`, `est:`, a readiness set, a gate label). Label sync touches **only those**. It never removes or adds a status, type, risk or any label a person added by hand, so a mirror never undoes it and it never undoes a mirror.
- **Keep the mapping in the tree** (a sidecar file: for each entry, its area, kind, size and whether it gates a launch). A new entry gets its row in the same change that adds it. Computed labels (readiness) come from the tracker: for an open entry in backlog or in progress, "blocked on a person" if it needs one, else "blocked by an issue" if an open unbuilt issue blocks it, else "ready". A built entry has none.
- **Dry run first.** Compute what would change (labels to create, labels to add or remove per issue) and print it; show the diff (counts and a sample) before applying. Apply only after the pass's go-ahead covers label changes (it does when the write-list had the "labels" line).
- **Create missing labels** (with the project's colors) before applying them. Never `--force` a label that exists: it recolors a label the repo already has. Compare names ignoring case.
- **Idempotent.** Running it twice changes nothing the second time; verify that by running the dry run again and expecting an empty diff.
- Run it after every publish and every status change.

A sketch of the dry run in GitHub terms (no script is shipped here; the shape is the point):

```bash
# what each open issue has now
gh issue list --state open --limit 1000 --json number,labels,blockedBy,assignees
# what the tree says it should have (sidecar rows + computed readiness) -> a table: issue, add[], remove[]
# print the table; apply only after the go-ahead:
gh label create "<name>" --color <hex>          # only if missing
gh issue edit <n> --add-label "<l1>,<l2>" --remove-label "<l3>"
```

Limits: `gh issue list --limit` is capped by what you ask for, so ask for more than the repo has; and a blocked-by read only sees direct blockers.
