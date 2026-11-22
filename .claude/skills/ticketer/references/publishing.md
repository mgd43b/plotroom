# Publishing to the tracker

The tracker is a mirror of the tree. Publishing is every write to it: creating issues, editing bodies, labels, relations, assignees, comments, closing. Each is outward-facing and mostly hard to undo, so it happens once, from a reviewed list, on the human's word.

## The write-list

Before any write, produce the write-list ([templates/write-list.md](../templates/write-list.md)). It names, for each write: the target (an existing issue number or "new"), the change (create, edit body, labels, parent, blocked-by, assignee, comment, close), the entry it comes from, and for comments the exact text. Group it by kind (creates, body edits, relations, labels, comments, closes). Anything you would do that isn't on the list, you don't do.

Review the list yourself before you show it:

- Every write traces to an entry that changed in this pass.
- No body edit overwrites a change someone made on the tracker side since the last mirror (read the issue first; if the bodies differ, show both and ask).
- No ticket is edited while an open PR edits its file ([sequencing](tree-pass.md#sequencing-with-open-prs)).
- No closing keyword appears in any body or comment text.
- Parents exist before their children; blockers before the tickets they block.
- Comments are short and standalone, and anything for the human is assigned to them.
- Epic counts stay under the limits ([epics-and-labels.md](epics-and-labels.md#epic-size)).

Show the human the list in the same message that asks for the go-ahead, with the shortest honest summary on top ("5 new issues, 18 body edits, 2 relations, 1 comment; nothing closed").

## The go-ahead

- **It is the human's own words, typed in this session** (the ticketer's), after they have seen the list. "Yes, publish" is a go-ahead; the coordinator saying "they said yes" is not.
- **Never relay.** Not from the coordinator, a lane, a PR comment or a note. If the human approved somewhere else, ask them to say it here. This is the same rule as for production deploys: the permission is checked where the action happens ([permission laundering](../../coordinator/references/routines.md#no-permission-laundering)).
- **A yes covers the list they saw**, and only that. Anything you add afterward (a late finding, a fix to a typo that changes meaning) goes on the list again and gets its own go-ahead. A cosmetic fix to your own text doesn't.
- **If a write is refused** (by the tracker, a permission prompt or the human), stop on that item, report it, and carry on only with the independent ones. Never retry it another way, and never ask another session to do it.
- Record the go-ahead's exact words on the write-list and in the decision log ([decisions.md](decisions.md)).

## The order

1. Create parents and prerequisites first (epics before their children, blockers before what they block).
2. **Create with content only.** Don't set parents or blockers in the create call. The tracker adds them after the issue exists; if that step fails, a create that carried them can fail without telling you the new id, and a rerun files the ticket twice.
3. Mirror the new id (and its url) into the tree **before anything else**.
4. Then add relations (parent, blocked-by), one by one, skipping any the issue already shows (adding one twice fails).
5. Then labels and assignees, then body edits to existing issues, then comments.
6. Closes last, and only for tickets whose verification is recorded.

If a create fails without an id, it may still have succeeded: before creating again, list the newest issues twice a few seconds apart (the list can trail a create) and look for the one whose title and body match. If none matches and the list doesn't reach back far enough, ask before creating again.

Worked example (GitHub issues, `gh` 2.94 or later for sub-issues and blocked-by):

```bash
# 1. create with content only; the url it prints is the id to mirror
gh issue create --title "<title>" --body-file <body.md> --label "<type>,<status>" --milestone "<initiative>"
# 2. then relations, leaving out any that already exist (check first)
gh issue view <n> --json parent,blockedBy
gh issue edit <n> --parent <epic-number> --add-blocked-by <n1>,<n2>
# 3. body edits, assignees, labels
gh issue edit <n> --body-file <body.md> --add-assignee <human>
# 4. status is a label swap; done closes the issue, dropped closes it as not planned
gh issue close <n> --reason "not planned"
```

Other trackers (Jira, Linear, Trello, Notion) have the same operations under different names: create, edit body, set parent or epic, link blocked-by, set status, assign, comment, close. Keep the same order and the same rules; write the project's mapping (tree type to tracker type, tree status to tracker status or label) once, in the project's own notes, and reuse it.

## Mirroring

After every write (and after any read before a board view), write back to the tree: the tracker id, its url, the tracker status, the assignee and the blockers, for each ticket. A ticket on the tracker that the tree doesn't know is **unmatched**: add an entry for it (a bare issue), mirror again, and bring its body into the tree. A published container (an epic) is mirrored like a ticket; tell the human when its tracker status differs from the tree's, and close it only when every child is closed.

## After publishing

- Verify each line of the write-list against the tracker (counts, ids, labels, relations). Record anything that didn't go through.
- Tell the coordinator, in a short message, the new and changed issue numbers and anything it must do (an order, an ask).
- Post any human-facing ask as its own short comment, assigned to them.
