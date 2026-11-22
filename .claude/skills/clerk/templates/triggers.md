# Clerk triggers

Only the coordinator writes or changes a trigger. The clerk fires each one once, marks it `FIRED <time> <merge sha>`, and logs it in ledger.md. Message text is sent as written, with the bracketed parts filled in, and signed "Clerk:". This file is per-run state with session ids: it lives in the state folder (`ORCH_STATE_DIR`), never in git.

A trigger names something exact: a PR number, or a branch-name pattern for a PR that does not exist yet. Never a description the clerk would have to judge ("the PR that fixes the flaky test"). Anything that needs a decision belongs in a message to the coordinator, not in a trigger's text.

## T1: #<N> merges (tell a waiting lane)
- Message `<lane channel>`: "Clerk: #<N> merged at [sha8]: <what is now on the base branch>. <The one step the lane takes next, with a limit: merge the base branch into #<M> once, push once; it merges on green.>"
- Tell the coordinator.

## T2: branch `<pattern>` opens a PR that merges
- Message `<lane channel>`: "Clerk: #[N] merged at [sha8]. <the next step the coordinator already decided, with its limits (a spend cap, which keys, when to stop). Report the result to the coordinator.>"
- Tell the coordinator.

## T3: #<N> merges (the coordinator decides what follows)
- Tell the coordinator: "#<N> merged at [sha8]: <what is now on the base branch>; <the decision is yours>."

## T4: any other open PR merges (the standing default)
- If its owner is a lane in the ledger with a message channel, message the lane: "Clerk: #[N] merged at [sha8]. Do the post-merge steps you own, and send your post-merge list's status to the coordinator."
- If its owner is `coordinator-only` (a subagent, or a worker with no channel to the clerk), tell the coordinator.

## T5: #<N> merges (a lane whose post-merge list is already empty)
- Owner: <lane> `<channel>`. Its post-merge list is "none".
- Tell the coordinator: "#<N> merged at [sha8]: <lane>'s list is empty; archive it." Don't message the lane.
