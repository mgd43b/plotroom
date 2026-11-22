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

## Git

- Do not add attribution lines or `Co-Authored-By` trailers to commit messages or PRs.
