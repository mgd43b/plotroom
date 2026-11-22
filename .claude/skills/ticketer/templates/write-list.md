# Write-list: tree pass <date> (<PR or branch>)

Status: HELD, waiting for the human's go-ahead, typed in the ticketer's session. Nothing below has been done.
(After publishing: `PUBLISHED <date> on "<the human's exact words>"`, kept as the record.)

Summary: <N new issues, N body edits, N relations, N label changes, N comments, N closes. Nothing else.>

## Creates (in this order)
| # | Entry | Type | Title | Parent (epic) | Blocked by | Labels | Assignee |
|---|---|---|---|---|---|---|---|
| 1 | <ref> | <bug/story/spike/flake> | <title> | <epic issue> | <issues> | <labels> | <human, only if it needs them> |

## Body edits (existing issues; body from the entry's file)
| Issue | Entry | What changed | Open PR touching the file? |
|---|---|---|---|
| #<n> | <ref> | <one line> | <none / PR #, sequenced how> |

## Relations
- #<n> blocked by #<m>
- #<n> parent #<epic>

## Labels (sync dry run, summary)
- Create: <labels>. Add: <n issues>. Remove: <n issues>. (Full dry-run table: <where>.)

## Comments (exact text)
**On #<n>** (assigned to <human>):
> <the full comment text; short and standalone, with options or steps>

## Closes (verification recorded for each)
- #<n>: <verified how>

## Not on this list, on purpose
- <things that wait: an item held for an open PR, a split that needs its own pass, a question that needs an answer first>
