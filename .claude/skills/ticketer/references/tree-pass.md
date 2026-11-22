# The tree pass

A tree pass is one bounded piece of work: take what has queued up (findings, decisions, merged work, bare issues), fold it into the tree, and produce a write-list for the human. It is a pull request like any other (one branch, one PR), plus a hand-over.

## Inputs

Collect these, and nothing else, at the start of the pass:

- **Findings** from the coordinator and the lanes: bugs, flakes, follow-ups, out-of-scope discoveries, each with evidence and an owner.
- **Decisions** the human made since the last pass, with their exact words and where they said them ([decisions.md](decisions.md)).
- **Merged work**: tickets whose PRs merged (status and a `Built: <PR>` note), and what verification they still need.
- **Bare issues** filed outside the tree, to fold in.
- **Drift**: tickets whose status in the tree disagrees with the tracker (a closed issue with an open entry, a stale label).
- **Open questions and assumptions** from earlier passes that a new fact now answers.

Read the previous hand-over first. If anything above is missing evidence, send it back once.

## Steps

1. **Orient.** Read the tree's status (what is ready, blocked, in progress, done) and the open PRs. List the open PRs and the files each touches ([sequencing](#sequencing-with-open-prs)).
2. **Fold each finding into an entry**, or bind it to the entry that already exists (search first: a duplicate is worse than a gap). An entry has a title a person can scan, a type (bug, story, spike, flake, chore), a one-paragraph description, acceptance criteria you could check, what it is blocked by, and what it covers. Flakes and bugs name the failing test or the repro and the run id.
3. **Batch.** Group tickets that are the same shape into one entry with a checklist, or one epic of siblings, so one lane can take the batch. Say so in the hand-over.
4. **Fold in bare issues**: create the entry, bind the issue's number to it (mirror the id), and republish its body from the entry later (with the go-ahead). Don't close and recreate.
5. **Update statuses from merged work**: done only for what is verified; `review` or `built` for what merged but still needs its check. Add the `Built: <PR>` note. Record verification when the verifying lane reports it.
6. **Record decisions** as `Decision:` lines in the entry's Notes (or the epic's, or the architecture or design document the project keeps decisions in), quoting the human. Record assumptions as `Assumption:` and unanswered questions as `Open question:`, each with who can settle it. When a ticket can't proceed without the human, that is an ask ([the queue](../SKILL.md#the-rules), rule 10), not a buried question.
7. **Check sizes and shapes**: epic sizes ([epics-and-labels.md](epics-and-labels.md#epic-size)), entries too big for one PR (split), entries with no acceptance criteria (write them or mark them `Open question:`), dependency cycles, an entry blocked by something that is done.
8. **Update label rows** for new or changed entries, in the same change ([epics-and-labels.md](epics-and-labels.md#label-sync)).
9. **Write the write-list** ([templates/write-list.md](../templates/write-list.md)): every tracker write the pass would make, from the entries you changed. Nothing is published yet.
10. **Commit and open the pass's PR** (tree files only). Write the **hand-over** ([templates/handover.md](../templates/handover.md)) in the PR body or the coordinator's notes folder, and send the coordinator the short version.
11. **Stop and wait for the human's go-ahead**, typed in your session ([publishing.md](publishing.md)). While waiting, do nothing in the tracker. The write-list is kept (in the PR or the notes folder); it is the record.

After the go-ahead: publish in the order the write-list says, mirror ids back, run the label sync (dry run first), verify the result against the write-list line by line, and amend the PR (or add a commit) with the mirrored ids. Mark the write-list `PUBLISHED <date> on "<the human's words>"`. Keep it as the record.

## Sequencing with open PRs

The tree's files are edited by lanes too: a lane marks its ticket `Built:`, a lane updates a story's notes. If the pass changes, renames or moves a file an open PR also changes, both PRs conflict, and so does every PR that was based on the old path. One pass moved story files that five open PRs edited.

- **Before** you change, move, rename, split or republish anything, list the open PRs and the files each touches. For every overlap, tell the coordinator: "These entries are edited by open PRs #A, #B: I'll hold the move until they merge, or you tell me the order."
- **Hold** moves and renames until the overlapping PRs merge; do content-only changes to files no open PR touches.
- **Never** rebase or rewrite a lane's PR, and never edit a lane's branch. If the pass needs a lane's change, ask the lane.
- After the PRs merge, merge the base branch into the pass's PR once and re-check the overlap.
- Tracker-side edits follow the same rule: don't republish a body (or change its labels) from the tree while a lane's open PR changes that ticket's file; the mirror would publish a stale body or undo the lane's change.
- Re-slicing an epic (moving entries between epics) is the biggest move: do it in its own pass, after everything touching those entries has merged.

## Quality bar for an entry

A lane building from the entry must be able to start without asking: the goal, the acceptance criteria, the owned paths if known, what it depends on, and what is out of scope. If you can't write acceptance criteria, it is a spike or an open question, not a story.
