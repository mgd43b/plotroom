---
title: Mount per-project credential Secrets in the Helm chart
type: feature
ticket: 2
created: '2026-11-27'
status: built
baseline_revision: '7e06a751ff137efd1388bc2a23fd23686f1d82cc'
route: full
route_source: auto
risk: medium
review: thorough
---

## Intent

Let operators mount caller-created Kubernetes Secrets for the GitHub App,
PAT, and embedder in one project without writing credential bytes to chart
values, rendered configuration, logs, or evidence.

## Local evidence

- Helm rejects unknown projects, missing mapping fields, and an ambiguous
  GitHub mode. Its rendered default and token Job mounts preserve the
  previous chart. Only the server mounts the selected keys, at mode 0440.
- On a new disposable kind cluster based on the pinned source, an in-process
  synthetic transport verified the mounted App key, PAT, and embedder key
  through the server. Both GitHub projects projected a PR and reported
  readiness. The embedder authenticated with its mounted bearer.
- A separate release referring to a missing required key had a
  `FailedMount` event and zero ready replicas. A scan of the rendered YAML,
  live ConfigMap, and server logs found no synthetic credential bytes.
- The embedder tests cover missing and malformed key files, file refresh, and
  redirect isolation. Independent chart and runtime inspections passed.

## Opening review

Invalid key paths now return field-specific errors during config loading, and
direct adapter reads convert invalid paths into sanitized embedding failures.
The runbook distinguishes Helm mapping checks from daemon endpoint validation.

## Owner acceptance

Built: [PR #215](https://github.com/mgd43b/plotroom/pull/215), merged as
`d1904857f3c9b919a1ced078eee4c5e45d9892d5` with
[green main CI](https://github.com/mgd43b/plotroom/actions/runs/46007832740).

The November 27 owner-authorized run used a fresh local kind install and the
mounted App key. The non-root daemon read the read-only `0440` file, passed
readiness and authenticated MCP, and projected the dedicated repository's
existing PRs. The render, live ConfigMap, and logs contained no credential
material in the scan. The [story Notes](story-per-project-secrets-on-kubernetes.md#notes)
record the live evidence and cleanup.
