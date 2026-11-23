---
id: 1
type: spike
title: "Can plotroom read Claude desktop session state locally?"
parent: epic-one-inbox
covers: [R7]
after: []
hitl: false
risk: medium
tracker_id: "31"
remote: "https://github.com/mgd43b/plotroom/issues/31"
tracker_status: backlog
status: built
---

# Can plotroom read Claude desktop session state locally?

## Description

Find whether the Claude desktop app exposes session turn-end reports and messages to a local process, and how, behind the SessionReader port. Time-boxed to one session. When it finds a mechanism it removes entry 18's unknown (from its tickets.toml entry and its file); when it finds none, entry 18 is dropped. Hands on: the answer to entries 9 and 18 and to the epic's Notes.

## Requirements

Verify: The mechanism, or its absence, recorded in the epic's Notes with a working probe or the evidence that none exists, and entry 18 either unblocked or dropped to match.

## References

- parent — _bmad-output/initiative-orchestration-control-plane/tickets/epic-one-inbox/epic-one-inbox.md

## Notes

- Answer: a mechanism exists (the process registry, the app's per-session state file and the transcript, read-only); recorded with the probe in the epic's Notes, under "Finding (entry 1, #31)". Entries 9 and 18 take their key and limits from it.
