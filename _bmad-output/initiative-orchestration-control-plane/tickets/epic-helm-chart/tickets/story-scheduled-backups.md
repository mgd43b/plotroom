---
id: 4
type: story
title: "Scheduled backups"
parent: epic-helm-chart
covers: [R1]
after: [1, 1.14]
hitl: false
risk: low
tracker_id: "124"
remote: "https://github.com/mgd43b/plotroom/issues/124"
tracker_status: backlog
status: built
---

# Scheduled backups

## Description

An optional CronJob calls the server's online-backup command (orchestrator token) on a schedule, which writes a consistent backup of every project file to a separate backup volume, keeping a configured number.

## Requirements

Verify: On kind, the CronJob run produces one consistent backup per project file on the backup volume that opens and migrates, and older backups past the configured count are removed.

## References

- parent — _bmad-output/initiative-orchestration-control-plane/tickets/epic-helm-chart/epic-helm-chart.md

## Notes

- Decision: 2026-11-22, backups go through a server command into their own volume, since a second pod cannot share the ReadWriteOnce data volume across nodes and backups should not live on the volume they protect.

- Built: https://github.com/mgd43b/plotroom/pull/185
