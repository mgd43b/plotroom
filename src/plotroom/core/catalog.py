"""The catalog: the one place later stories register event kinds and commands."""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import partial

from plotroom.core.actions import ACTION_FIELDS, FAILED_ACTION
from plotroom.core.commands import Command, CommandRegistry
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
    owns_lane,
    ready_lane,
    start_lane,
    update_lane,
)
from plotroom.core.next_items import ItemKind, ack, owns_ack
from plotroom.core.roles import WriterRule, WriterRules
from plotroom.core.watchers import MISSING_WATCHER
from plotroom.core.work_items import IDLE_WORKER


@dataclass(frozen=True)
class Catalog:
    events: EventRegistry
    commands: CommandRegistry
    items: dict[str, ItemKind] = field(default_factory=lambda: {})

    def register_item(self, kind: ItemKind) -> None:
        if kind.name in self.items:
            raise ValueError(f"next item kind {kind.name!r} is already registered")
        self.items[kind.name] = kind

    @property
    def writer_rules(self) -> WriterRules:
        return self.events.writer_rules


def build_catalog(
    workers: frozenset[str] = frozenset(), *, github_repository: str | None = None
) -> Catalog:
    """Every kind and command the server knows."""
    events = EventRegistry()
    catalog = Catalog(events, CommandRegistry(events))
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
    events.register(EventKind("core.tick", (), frozenset({"system"})))
    for name, fields in GITHUB_FIELDS.items():
        events.register(EventKind(f"github.{name}", (REPOSITORY, *fields), frozenset({"system"})))
    for kind in PR_KINDS:
        events.register(EventKind(kind, PR_FIELDS, frozenset({"system"})))
    events.register(
        EventKind("switchboard.message", (FieldSpec("sender", "str"),), frozenset({"system"}))
    )
    events.register(EventKind(TURN_REPORT, TURN_FIELDS, CLAUDE_APPENDERS))
    events.register(EventKind(SESSION_MESSAGE, (SESSION_FIELD,), CLAUDE_APPENDERS))
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
    owner = (*orchestrator, WriterRule("worker", owns_lane))
    catalog.commands.register(
        Command(
            "lane_start",
            (
                lane,
                FieldSpec("worker", "str"),
                FieldSpec("branch", "str"),
                FieldSpec("work_items", "array"),
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
            (lane, FieldSpec("pr", "int"), FieldSpec("head_sha", "str")),
            partial(ready_lane, repository=github_repository),
        ),
        rules=owner,
    )
    catalog.commands.register(
        Command("lane_archive", (lane,), partial(archive_lane, workers=workers)), rules=orchestrator
    )
    return catalog
