---
id: 3
type: story
title: "Run reports, run_finished and main_green_with_images"
parent: epic-triggers
covers: [R1, R4]
after: [2, 1.8, 2.3]
hitl: false
risk: medium
tracker_id: "57"
remote: "https://github.com/mgd43b/plotroom/issues/57"
tracker_status: backlog
---

# Run reports, run_finished and main_green_with_images

## Description

Adds a run_report command a lane's worker token uses for its own lane's runs (run id, state), the run_finished(id) leaf over it, and main_green_with_images(sha?) holding once main's latest completed run is green and the configured image-build checks on that commit passed.

## Requirements

Verify: A lane's run_report of a run finishing fires run_finished once, another lane's token is refused, and main_green_with_images holds only after both CI and the image-build checks pass on the same main commit.

## References

- parent — _bmad-output/initiative-orchestration-control-plane/tickets/epic-triggers/epic-triggers.md
