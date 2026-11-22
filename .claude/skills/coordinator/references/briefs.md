# Briefs

Every brief is complete: a lane starts with only what you put in it. Name the ticket and where its description is, the paths the lane owns, the plan's shape, what done means, and who to report to. Point to the project's rules file for the project's rules; don't restate them. Never put a secret in a brief (say where it is, not what it is). Briefs are for any worker: Claude or Codex, started by you (Claude Code can start a session from a tool, on the human's turn) or by the human, who pastes the brief as the session's first message.

## A lane brief

Start one per self-contained task, in its own worktree (`<lane-model>`, high effort). Fill in the placeholders; delete the clause for the kind you aren't using.

```text
You are a lane for <project> (<owner>/<repo>). The coordinator is <COORDINATOR_ID>; report to it over <channel: session message by id, or relay "<name>">.

Task: <ticket number and title>. Description: <where the ticket and its acceptance criteria are>. <One paragraph: what and why, with the human's exact words if they decided anything.>

Read <rules file> and follow it.

Scope: <what is in>. Not in scope: <what is out; anything else you find goes to the ticket tree or the coordinator, not this PR>.
Owned paths: <files and folders this lane may change; nobody else touches them while it runs. Another lane owns: <paths>>.
Plan shape: <the steps and the order; the tests that prove it (run only the ones you changed, with at most <N> workers); the fixtures or contracts it relies on>.
Limits: <paid runs: the key, the trial sample, the cap, the stop condition. Production or deploys: not without the human's go-ahead typed in this session. Staging: <what is allowed>.>

You own your PR, and one PR at a time. When you open it:
 - Claude Code: bind the PR to this session and turn Auto-fix on, so CI failures and review comments reach you.
 - Codex or any session without Auto-fix: after every push, watch the PR (`gh pr checks`, review threads) until every check is green and every thread is answered and resolved. Don't wait for someone to tell you.
Fix what is real. Never rerun a failing test to get green: if a test fails and then passes, it is a bug; fix it, or file it for the coordinator with the test id and run id.

Done when: one PR is open, CI is green on its head, every review thread is answered and resolved, and the ticket has its status and a "Built: <PR>" note. Never merge; the coordinator's sweep does. Plain git commit with the repo's configured identity, never force-push, no attribution of any kind in commits, PRs or files, American English.

Then send me READY (below). Anything that waits on the human is a short, standalone comment on its issue, assigned to them, not a line in a message.
```

## The READY message (what a lane sends when it is done)

```text
READY #<PR>
Head: <sha8>
Changed: <files or areas>
Tests run: <the ones you changed, and the result>
Left out, and why: <anything in scope you didn't do>
Post-merge list: <every step the task still needs after the merge (a staging check, a posting, an issue comment, an unassign, a follow-up ticket), each with its owner; or "none">
```

The coordinator checks the PR (CI green on that head, threads resolved), then the gate merges it. The lane then does its post-merge list. When the list is empty, the coordinator archives the lane.

## Reusing a lane

A lane that just merged can take a follow-up on its own PR or the next step of what it just did, with a short message that has the same four parts: the task, the owned paths, the limits, and "done when". Anything else gets a fresh session.

## Starting a worker when no tool can start one

Propose it to the human with the brief ready to paste: "Start a new session in `<repo>` (<claude or codex>, `<lane-model>`), and paste this as its first message." Add the one line the lane needs to reach you: your session id or the relay name. Record the lane in the ledger when it says `ACK`.
