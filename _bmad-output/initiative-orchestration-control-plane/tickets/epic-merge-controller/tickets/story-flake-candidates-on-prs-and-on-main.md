---
id: 6
type: story
title: "Flake candidates on PRs and on main"
parent: epic-merge-controller
covers: [R6]
after: [2.3, 2.5, 2.15]
hitl: false
risk: medium
tracker_id: "65"
remote: "https://github.com/mgd43b/plotroom/issues/65"
tracker_status: done
---

# Flake candidates on PRs and on main

## Description

A check that fails and then passes on the same head SHA, a PR failure in a test whose files the PR does not change, or a main failure whose failing test passes on a later main commit that did not change its files, opens a flake-candidate item with the test id and excerpt taken from structured check-run annotations or a JUnit artifact (or the check name and run link when neither exists); nothing is ever rerun. Flake candidates are kept as records epic 10 indexes.

## Requirements

Verify: Fixture runs for each of the three cases open one flake item each with test id and excerpt, a run with no annotations opens one with the check name and run link, and no rerun call is made.

## References

- parent — _bmad-output/initiative-orchestration-control-plane/tickets/epic-merge-controller/epic-merge-controller.md

## Notes

- Decision: 2026-11-22, a test's area is its own files, taken from the annotation or JUnit path; flakes on main are judged across later main commits, since nothing reruns.
