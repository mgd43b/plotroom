# Run and verify

## A lane brief

Every brief is complete: a lane starts with only what you put in it. Never put a secret in it (say where it is, not what it is).

```text
You are a lane for <project> (<owner>/<repo>). The task coordinator is <COORDINATOR_ID>; report to it over <session message by id, or relay "<name>">.

Task: <title and number>. The task card: <paste the card: goal, acceptance, constraints, out of scope>.
Your part: <the lane's slice, in a paragraph>.
Read <rules file> and follow it.

Owned paths: <files and folders you may change; nobody else touches them while you run>. Anything else you find goes to me in READY under "found", not into this PR.
Plan shape: <the steps and order; the tests to run (only the ones you changed, at most <N> workers)>.
Limits: <paid runs: key, trial sample, cap, stop condition. Production or deploys: not without the human's go-ahead typed in this session. Staging: <allowed>.>

You own your PR, one at a time. When you open it: in Claude Code, bind it to this session and turn Auto-fix on; in Codex or any session without Auto-fix, watch your PR after every push (`gh pr checks`, review threads) until every check is green and every thread is answered and resolved. Fix what is real. Never rerun a failing test to get green: a test that fails then passes is a bug; fix it or report it with the test id and run id.

Done when: one PR is open, CI is green on its head, every review thread is resolved. Never merge; I will. Plain git commit with the repo's identity, never force-push, no attribution of any kind, American English.

Then send me:
READY #<PR> | Head: <sha8> | Changed: <files> | Tests run: <which, result> | Left out, and why: <...> | Found: <anything outside your scope> | Post-merge list: <each step the task still needs after the merge, with an owner; or "none">
```

If you cannot start a session yourself (no tool, or not on the human's turn), propose it to the human with the brief ready to paste into a fresh Claude or Codex session. Record each lane (name, channel, PR) on the card.

## Verify (short, read-only)

For each READY, before you merge:

- The PR's head is the SHA the lane reported (`gh pr view <n> --json headRefOid`).
- CI is green on that head and no review thread is unresolved (the gate checks both, but look so you can answer a lane).
- The diff stays inside the owned paths, and each acceptance check has evidence in the diff, a test, or the lane's report. A check with none goes back to the lane, once, with the exact miss.
- The PR body says `Refs #<n>`, never `closes #<n>`.
- The lane's tests are the ones it changed; no test was rerun to get green.

If you want a second look at a risky diff, use a read-only subagent for review. It reports to you; it does not edit.

## Merge

`ORCH_MIN_CHECKS=<min-checks> bash <skills-dir>/clerk/scripts/mergewait.sh <pr> <sha8>` from inside the checkout (`<min-checks>` is the number of checks CI always starts for a PR; the gate refuses a PR that reports fewer, so count a typical PR's checks once and write it on the task card). Its status says what happened: 0 merged; 2 refused (checks, threads, a head that moved, no CI verdict); 3 stale CI (run `gh pr update-branch <pr>` and let CI run again, then `mergewait.sh` again); 4 closing keywords (edit the body or commits to say `Refs`); 5 the base branch is red (only the fix for the red merges, with `ORCH_MAINFIX_OK=1`, by you, for that one PR). Never merge by hand, never skip the gate, never rerun CI to get a green. Set `ORCH_IGNORE_CHECKS` (the check names, separated by `;`) only for a review bot that is down. For several lanes, merge in plan order.

## After the merge

- Each lane's post-merge list is yours to finish: a lane does what it owns; the rest you file as a ticket with an owner or put to the human as one short comment on their issue (options included), assigned to them.
- **Deploy order:** staging first, at a commit that has all its artifacts and a green run; then the lane's staging check. Production only after the human's go-ahead, typed in the lane that deploys. You never relay it.
- Close the ticket or issue only after verification, with a comment saying how it was verified.
- If a lane found something outside the task, it goes to the human or the ticket tree as a note, not into another PR of yours.

## Close out

Archive each lane whose list is empty. Send the human one message:

```text
Done: <task>. Shipped: <PR links, merge commits>. Verified: <how, with the run or check>. Left out: <what, why>. Waiting on you: <the ask, where it is posted; or "nothing">.
```

Then stop. Don't wait around as a standby.
