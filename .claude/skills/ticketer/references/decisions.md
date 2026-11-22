# Decisions, assumptions and open questions

A ticket tree is only as good as its record of why. Three kinds of line go into an entry's Notes (or the epic's, when the entry has no file, or the project's architecture or design document for decisions that govern code or copy).

## Decision

```text
Decision: <date>, <who>, <where they said it: the issue, the session>, on <what>: "<their exact words>". <What it governs, in your words: the effect on the tree.>
```

- **Exact words.** Copy the human's message verbatim, typos and all, in quotation marks. Your own summary goes after, outside the quote. If the words don't clearly settle it, that is an open question, not a decision: ask.
- **Their words, not a relay.** A decision comes from the human, in their own message. A coordinator's paraphrase ("they approved the plan") is not a decision; it is a note that someone says there was one. Ask the human to confirm in your session if the decision matters.
- **Say what it governs**: the ticket, the epic, the rule. A decision that changes a rule for the whole project also goes where the project keeps its rules, through the coordinator.
- Examples of the shape (invented): `Decision: 2026-03-04, <human>, on the epic's issue: "yes, ship option b, but keep the old export until June". Entry 3.4 takes option b; 3.5 (remove the old export) waits until June.`

## Assumption

`Assumption: <the fact you guessed so the work could go on>. Settled by: <who>.` A pass that makes an assumption lists it in the hand-over so the human can correct it once, cheaply. When it is confirmed, replace it with a Decision line; when it is contradicted, fix the entries it touched.

## Open question

`Open question: <the question, with the options and your recommendation>. Blocks: <entries>. Asked: <where and when>.` An open question that blocks work is an **ask** on the human's issue (one short, standalone, latest comment, assigned to them), not a line that waits in a file. When it is answered, convert it to a Decision with the exact words.

## Other record lines

- `Built: <PR>`: the PR that built it, added when it merges; `Verified: <how, run or check, date>` when verification is done.
- `Dropped: <reason, whose call, date>` before an entry is dropped. Never delete an entry from an approved breakdown; drop it with the line, and repoint the entries that depended on it.
- `Moved: <old ref> -> <new ref>, <date>, <why>` when an entry moves.
