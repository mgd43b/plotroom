# Starting and using a clerk

The clerk keeps the merge sweep running and the ledger current, so your turns go to decisions. It decides nothing (its limits are in the `clerk` skill). Start one at the beginning of the role, and again if it is archived or loses focus. A single-task coordinator doesn't need one.

## Start it

1. Create the state folder and the files: from inside the project's checkout, `bash <skills-dir>/clerk/scripts/init-state.sh` (prints the folder; `ORCH_STATE_DIR` overrides the default). The scripts also need `ORCH_MIN_CHECKS`, the number of checks CI always starts for a PR (count a typical PR's checks once); put it, and `ORCH_CI_WORKFLOW` and `ORCH_IGNORE_CHECKS` if the project needs them, in the clerk's goal and in your own environment for `safemerge.sh`. The ledger and triggers hold session ids and are per-run: they stay out of git.
2. Fill in `ledger.md` from `gh pr list --state open` and the linked sessions: each PR's owner, kind (Claude, Codex, subagent), channel, state, and what happens after its merge. Fill in `triggers.md` for every "when X merges, send Y" you already know (the template has the shapes). Add any PR the sweep must leave alone to `skip.txt`.
3. Start a fresh session in the project's checkout (on the human's turn, [lesson 10](lessons.md)); a cheaper model is fine. Its brief is `goals/clerk.txt` (or `prompts/clerk.md`) with the placeholders filled in (your id, your channel, the state folder), plus "take the role from the clerk skill".
4. Wait for "Clerk: sweep running (task <id>), ledger read." Then **stop your own sweep**: two sweeps race to merge and to update branches.

## Use it

- **Triggers are yours alone.** Add one to `triggers.md` whenever a merge should cause a message (a flake fix landing should tell the waiting lanes; a deploy-ready PR should tell you). A trigger names a PR number or a branch pattern, never a description. The clerk fires each once and marks it FIRED. Anything that needs a decision belongs in a message to you, not in a trigger's text.
- **Keep the ledger true.** The clerk updates it after every merge; you add the PRs and lanes it doesn't know about (a subagent's PR, a new worker). Read it first after a compaction ([lesson 8](lessons.md)).
- **Red base branch:** the clerk stops merging on its own and tells you; it is the only reason the sweep stops. Deploys do not pause merges. If you need the sweep stopped for another reason, message "Coordinator: stop the sweep", and "resume" later.
- **What it sends you** is batched: triggers addressed to you, merges made outside the gate, flake candidates (test id, run id, 3 log lines), a red base branch, and any decision a lane asked for, forwarded word for word. Decide, and answer the lane yourself.
- **It never decides.** If it tells a lane "that's the coordinator's call", the question is now yours.
- **Stand it down** when the work is done or you are handing over: "Coordinator: stand down".
- Sign your messages to it "Coordinator:" so it can tell yours from a lane's.
