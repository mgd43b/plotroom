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

## Owner acceptance pending

After the exact merged revision passes main CI, the authorized owner chat can
build that revision and load the image into a new disposable kind cluster
with its own kubeconfig. In the release namespace, use the approved mounted
App key as the source for the selected Kubernetes Secret key. Configure one
project for `mgd43b/plotroom-test` with `projectSecrets.<project>.github.app`
and the existing public client and installation IDs. Use the App example in
`docs/running.md` and keep the key out of the values file.

Run `helm template` and inspect the server mount and TOML paths. Install only
in the disposable kind cluster with the locally loaded image. Verify
`/readyz` and an authenticated project snapshot with a fresh available
GitHub projection matching the dedicated repository. Compare the render,
ConfigMap, and sanitized logs with the mounted key in-process without
emitting credential material. Consume the disposable local bearer and remove
the cluster and its temporary resources. Keep #122 open until this live
witness is recorded.
