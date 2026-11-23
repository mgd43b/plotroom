"""The catalog: the one place later stories register event kinds and commands."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from functools import partial

from plotroom.core.actions import ACTION_FIELDS, FAILED_ACTION
from plotroom.core.branch_updates import (
    BRANCH_UPDATE_CONFLICT,
    BRANCH_UPDATE_FIELDS,
    BRANCH_UPDATE_STAGED,
    BRANCH_UPDATED,
    BRANCH_UPDATED_FIELDS,
)
from plotroom.core.briefs import BRIEF
from plotroom.core.checklists import CHECKLIST_ARG, CHECKLIST_EMPTY, CHECKLIST_EMPTY_FIELDS
from plotroom.core.claims import CLAIM_OVERLAP, PR_CLAIM_OVERLAP, TICKET_TREE_MOVE, claim, release
from plotroom.core.commands import Command, CommandRegistry
from plotroom.core.deploy_window import freeze, unfreeze
from plotroom.core.events import EventKind, EventRegistry, FieldSpec
from plotroom.core.github import PR_FIELDS, PR_KINDS
from plotroom.core.github_projection import GITHUB_FIELDS, REPOSITORY
from plotroom.core.intake import (
    CLAUDE_APPENDERS,
    INBOUND_MESSAGE,
    SESSION_FIELD,
    SESSION_MESSAGE,
    TURN_FIELDS,
    TURN_REPORT,
)
from plotroom.core.lanes import (
    LANE_WAITING,
    archive_lane,
    checklist_done,
    owns_lane,
    ready_lane,
    start_lane,
    update_lane,
)
from plotroom.core.merge_controller import MERGE_EVENTS, MERGE_ITEMS
from plotroom.core.merge_flakes import (
    FAILURE_FIELDS,
    FLAKE_CANDIDATE,
    FLAKE_EVENT,
    FLAKE_FIELDS,
    MAIN_FLAKE_EVENT,
)
from plotroom.core.next_items import ItemKind, ack, owns_ack
from plotroom.core.outbox import complete_outbox, lease_outbox
from plotroom.core.reports import DEPLOY_ARGS, DEPLOY_FIELDS, DEPLOY_STATUS, deploy_report
from plotroom.core.roles import WriterRule, WriterRules
from plotroom.core.run_reports import RUN_FIELDS, RUN_ID, RUN_STATE, RUN_STATUS, run_report
from plotroom.core.trigger_actions import (
    ARCHIVE_ITEM,
    MARK_FIELDS,
    NOTIFICATION_ITEM,
    DeliveryRoute,
)
from plotroom.core.triggers import (
    TRIGGER_FIRED,
    TRIGGER_FIRED_FIELDS,
    LeafRegistry,
    build_leaf_registry,
    cancel_trigger,
    create_trigger,
)
from plotroom.core.watchers import MISSING_WATCHER
from plotroom.core.work_items import IDLE_WORKER


@dataclass(frozen=True)
class Catalog:
    events: EventRegistry
    commands: CommandRegistry
    items: dict[str, ItemKind] = field(default_factory=lambda: {})
    trigger_leaves: LeafRegistry | None = None
    workers: frozenset[str] = frozenset()
    read_roles: Mapping[str, frozenset[str]] = field(default_factory=lambda: {})

    def register_item(self, kind: ItemKind) -> None:
        if kind.name in self.items:
            raise ValueError(f"next item kind {kind.name!r} is already registered")
        self.items[kind.name] = kind

    @property
    def writer_rules(self) -> WriterRules:
        return self.events.writer_rules


def build_catalog(
    workers: frozenset[str] = frozenset(),
    *,
    github_repository: str | None = None,
    delivery_routes: Mapping[str, DeliveryRoute] | None = None,
    image_jobs: tuple[str, ...] = (),
    lane_idle_minutes: int = 15,
) -> Catalog:
    """Every kind and command the server knows."""
    events = EventRegistry()
    leaves = build_leaf_registry(image_jobs=image_jobs, lane_idle_minutes=lane_idle_minutes)
    catalog = Catalog(
        events,
        CommandRegistry(events),
        trigger_leaves=leaves,
        workers=workers,
        read_roles={BRIEF: frozenset({"orchestrator"})},
    )
    catalog.commands.register(
        Command(
            "ack",
            (FieldSpec("item", "str"), FieldSpec("outcome", "str", required=False)),
            ack,
            record_if=lambda result: bool(result["changed"]),
        ),
        rules=(
            WriterRule("orchestrator"),
            WriterRule("worker", owns_ack),
            WriterRule("clerk", owns_ack),
            WriterRule("bridge", owns_ack),
        ),
    )
    catalog.register_item(ItemKind(FAILED_ACTION, 10, False, ("Inspect the failed action",)))
    catalog.register_item(
        ItemKind(BRANCH_UPDATE_CONFLICT, 10, False, ("Resolve the branch update conflict",))
    )
    events.register(EventKind(BRANCH_UPDATE_STAGED, BRANCH_UPDATE_FIELDS, frozenset({"system"})))
    events.register(EventKind(BRANCH_UPDATED, BRANCH_UPDATED_FIELDS, frozenset({"system"})))
    for kind, fields in MERGE_EVENTS.items():
        events.register(EventKind(kind, fields, frozenset({"system"})))
    for item_kind in MERGE_ITEMS:
        catalog.register_item(item_kind)
    events.register(EventKind("core.tick", (), frozenset({"system"})))
    events.register(EventKind(CHECKLIST_EMPTY, CHECKLIST_EMPTY_FIELDS, frozenset({"system"})))
    events.register(EventKind(FLAKE_EVENT, FLAKE_FIELDS, frozenset({"system"})))
    events.register(EventKind(MAIN_FLAKE_EVENT, FAILURE_FIELDS, frozenset({"system"})))
    catalog.register_item(ItemKind(FLAKE_CANDIDATE, 10, False, ("Investigate the flaky check",)))
    for name, fields in GITHUB_FIELDS.items():
        events.register(EventKind(f"github.{name}", (REPOSITORY, *fields), frozenset({"system"})))
    for kind in PR_KINDS:
        events.register(EventKind(kind, PR_FIELDS, frozenset({"system"})))
    events.register(
        EventKind(
            "switchboard.message",
            (FieldSpec("sender", "str"), FieldSpec("worker", "str", required=False)),
            frozenset({"system"}),
        )
    )
    events.register(EventKind(TURN_REPORT, TURN_FIELDS, CLAUDE_APPENDERS))
    events.register(
        EventKind(
            SESSION_MESSAGE,
            (SESSION_FIELD, FieldSpec("worker", "str", required=False)),
            CLAUDE_APPENDERS,
        )
    )
    catalog.register_item(
        ItemKind(INBOUND_MESSAGE, 20, False, ("Read and acknowledge the message",))
    )
    for status in ("attempt", "done", "failed"):
        events.register(EventKind(f"core.action_{status}", ACTION_FIELDS, frozenset({"system"})))
    catalog.register_item(ItemKind(LANE_WAITING, 20, True, ("Reply to the worker",)))
    catalog.register_item(ItemKind(IDLE_WORKER, 40, True, ("Start a lane for the worker",)))
    catalog.register_item(
        ItemKind(MISSING_WATCHER, 30, True, ("Start plotroom watch for this role",))
    )
    lane = FieldSpec("lane", "str")
    orchestrator = (WriterRule("orchestrator"),)
    catalog.commands.register(
        Command("freeze", (FieldSpec("reason", "str"),), freeze), rules=orchestrator
    )
    catalog.commands.register(Command("unfreeze", (), unfreeze), rules=orchestrator)
    owner = (*orchestrator, WriterRule("worker", owns_lane))
    events.register(EventKind(DEPLOY_STATUS, DEPLOY_FIELDS, frozenset({"system"})))
    catalog.commands.register(
        Command(
            "deploy_report",
            (lane, *DEPLOY_ARGS),
            deploy_report,
            record_if=lambda result: bool(result["created"]),
        ),
        rules=(WriterRule("worker", owns_lane),),
    )
    catalog.register_item(ItemKind(CLAIM_OVERLAP, 10, True, ("Coordinate the lane claims",)))
    catalog.register_item(ItemKind(PR_CLAIM_OVERLAP, 10, True, ("Coordinate the PR and claim",)))
    catalog.register_item(
        ItemKind(TICKET_TREE_MOVE, 10, True, ("Coordinate the move with the editing PRs",))
    )
    globs = FieldSpec("globs", "array")
    catalog.commands.register(
        Command(
            "claim",
            (lane, globs, FieldSpec("reason", "str")),
            claim,
            record_if=lambda result: bool(result["changed"]),
        ),
        rules=owner,
    )
    catalog.commands.register(
        Command(
            "release", (lane, globs), release, record_if=lambda result: bool(result["changed"])
        ),
        rules=owner,
    )
    catalog.commands.register(
        Command(
            "lane_start",
            (
                lane,
                FieldSpec("worker", "str"),
                FieldSpec("branch", "str"),
                FieldSpec("work_items", "array"),
                FieldSpec("branch_pattern", "str", required=False),
            ),
            partial(start_lane, workers=workers),
        ),
        rules=orchestrator,
    )
    catalog.commands.register(
        Command("lane_update", (lane, FieldSpec("waiting_on", "str")), update_lane), rules=owner
    )
    catalog.commands.register(
        Command(
            "lane_ready",
            (lane, FieldSpec("pr", "int"), FieldSpec("head_sha", "str"), CHECKLIST_ARG),
            partial(ready_lane, repository=github_repository),
        ),
        rules=owner,
    )
    catalog.commands.register(
        Command("lane_archive", (lane,), partial(archive_lane, workers=workers)), rules=orchestrator
    )
    catalog.commands.register(
        Command(
            "checklist_done",
            (lane, FieldSpec("step", "str"), FieldSpec("ref", "str", required=False)),
            checklist_done,
            record_if=lambda result: bool(result["changed"]),
        ),
        rules=owner,
    )
    events.register(EventKind(RUN_STATUS, RUN_FIELDS, frozenset({"system"})))
    catalog.commands.register(
        Command("run_report", (lane, RUN_ID, RUN_STATE), run_report),
        rules=(WriterRule("worker", owns_lane),),
    )
    catalog.register_item(NOTIFICATION_ITEM)
    catalog.register_item(ARCHIVE_ITEM)
    events.register(EventKind("core.lane_marked", MARK_FIELDS, frozenset({"system"})))
    events.register(EventKind(TRIGGER_FIRED, TRIGGER_FIRED_FIELDS, frozenset({"system"})))
    catalog.commands.register(
        Command(
            "trigger",
            (
                FieldSpec("mode", "str", required=False),
                FieldSpec("predicate", "object"),
                FieldSpec("action", "object"),
                FieldSpec("lane", "str", required=False),
            ),
            partial(
                create_trigger,
                registry=leaves,
                repository=github_repository,
                delivery_routes=delivery_routes if delivery_routes is not None else {},
            ),
        ),
        rules=orchestrator,
    )
    catalog.commands.register(
        Command("cancel", (FieldSpec("trigger_id", "str"),), cancel_trigger), rules=orchestrator
    )
    drain = (WriterRule("drain"),)
    catalog.commands.register(
        Command(
            "outbox_lease",
            (),
            lease_outbox,
            record_if=lambda result: result["lease"] is not None,
        ),
        rules=drain,
    )
    catalog.commands.register(
        Command("outbox_done", (FieldSpec("act_id", "str"),), complete_outbox), rules=drain
    )
    return catalog
