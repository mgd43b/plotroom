# plotroom

Instructions for AI coding agents (Claude Code, Codex, and others). This is the single
source of truth; `CLAUDE.md` imports it.

## Language

Use American English everywhere: code, comments, docs, commit messages and PR text.

## BMad Method (v7 alpha)

This project uses the BMad Method, installed as skills.

- Skills live in `.claude/skills/` (Claude Code) and `.agents/skills/` (Codex). Both are
  copies of the same skills from `bmad-code-org/BMAD-METHOD`.
- Project configuration lives in `_bmad/config.toml`. Team overrides go in
  `_bmad/custom/config.toml`; personal overrides go in `_bmad/custom/config.user.toml`
  (git-ignored).
- Planning and build artifacts are written to `_bmad-output/`.
- Ask the `bmad` skill for help, the next step, updates, or to check the installation.
  `uv` is required.

## Multi-session work

Several sessions (Claude Code and Codex) work on this repository at once under the coordinator,
clerk, ticketer and task-coordinator skills in `.claude/skills/` (linked from `.agents/skills/`),
copied from `mgd43b/prompts`. [coordination/README.md](coordination/README.md) has the goals,
the clerk's settings and how this bootstrap ends. The rules every session follows:

- **Main stays green.** Merges go through the clerk's gate (`safemerge.sh`), never around it;
  only the fix for a red main merges while it is red.
- **A flaky test is a bug.** Never rerun a job to get green; a test that fails and then passes
  gets its own ticket and fix.
- **One open PR per lane.** New findings go to the ticket tree, not into new PRs.
- **A merged PR is not a finished task.** Each lane's READY message lists its post-merge steps,
  and the lane is archived only when they are done.
- **Tickets** live in BMad's tree under `_bmad-output/` and are mirrored to GitHub Issues on this
  repository. Sofia (`bmad-agent-po`) writes and publishes them, on the human's go-ahead; no bare
  issues. A lane builds its ticket with `bmad-build`.
- **No closing keywords** (`close`, `fix`, `resolve` with an issue number) in PRs or commits;
  write `Refs #n`. An issue closes after its work is verified.
- **Epic closure.** Verify the epic’s Requirements and Done-when evidence, run `bmad-retrospective`, and record its verdict and follow-up dispositions in the epic folder before closing the epic or marking its canonical status done. Child closure alone is insufficient.
- **What stays with the human (mgd43b):** production, legal, price and scope decisions, logins and
  publishing setup only they have (PyPI, ghcr.io), and paid-run caps. Each ask is one short,
  standalone, latest comment on their issue, with options.
- **Paid runs** need a provider-enforced cap, a small trial first, and a projection from the
  trial's actual spend.
- **Public repository.** No private infrastructure names, internal hosts or addresses, or secrets
  in code, docs, issues, commits or PRs.

## Git

- Do not add attribution lines or `Co-Authored-By` trailers to commit messages or PRs.
- Commit with the repository's configured identity; never override the author.
