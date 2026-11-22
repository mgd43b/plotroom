# Intake and plan

## The task card

Write it first, in your first message or a scratch file the human can see (not in the repository unless the project keeps such notes). Keep it to a screen.

```text
Task: <ticket number and title, or a one-line name for the prompt>
Goal: <one sentence: what is true when this is done>
Acceptance: <the checks that prove it; from the ticket, or written by you from the prompt and shown to the human>
Constraints: <rules file, paths not to touch, deadlines, spend caps, who decides what>
Out of scope: <what you will not do; where findings go instead>
Human: <who decides, and where their queue is>
Assumptions: <each fact you guessed, so the human can correct it once>
Done means: <merged, verified how, ticket status, post-merge steps>
```

Read the ticket and what it links to. Read the project's rules file. If the ticket has acceptance criteria, don't rewrite them: copy them, and add only what the rules file makes mandatory (tests, copy review, no closing keywords).

A prompt with no ticket: turn it into three to six acceptance checks you could verify, and show them to the human before the first lane starts unless the human said not to ask. Ask at most one question, with options and your recommendation.

## Sizing: is it one task?

It is one task when all of these hold:

- It has one goal a person could state in a sentence.
- It fits in one to three PRs that can each be reviewed on their own.
- It touches paths one to three lanes can own without overlapping.
- The ticket tree, the tracker and other tickets need at most a status update.

If not, say so and stop: "This is bigger than one task because <reason>. It wants the coordinator role, and a ticketer for the tree. I haven't started anything." Offer the one-sentence split you would suggest. Don't quietly take a bigger job.

## The plan

```text
Lane A: <what>; owns <paths>; tests: <which>; needs: nothing
Lane B: <what>; owns <paths>; needs: Lane A merged (then: merge the base branch in, one push)
Order: <A, then B>   Deploy: <staging check after merge; production only on the human's go-ahead, typed in the deploying lane>
Risks: <what could force a re-plan: an overlap, a paid run, a flake>
```

- Split by owned paths, so no two lanes edit the same file. Two lanes that need the same file become one lane or an order.
- A single lane is the default. Add a second only when the work is separable and parallel, or needs a different skill (a deploy lane, a content lane).
- Same-shaped sub-tasks (the same change in ten places) go to one lane as a batch.
- Anything that ends in a commit or PR goes to a lane in its own session, never to a subagent of yours. Subagents are for short read-only checks (reading a file, finding a caller).
- Name the check that would tell you the task is *wrong*, not just finished: a failing acceptance criterion, a surprise in the diff.

Show the plan to the human only when it changes what they asked for or spends something; otherwise note it on the card and start.
