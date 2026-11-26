"""Mutating commands (AD-2, AD-14): deduplicated by ``request_id`` and recorded as events.

A command's first run appends the handler's own events and then one ``core.<command>`` event with
``upstream_id = <role>:<request_id>`` holding the arguments and the result, all in one
transaction. A retry finds that event and returns its result without running the handler.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from plotroom.core.events import EventKind, EventRegistry, FieldSpec, LedgerError
from plotroom.core.roles import WriterRule
from plotroom.core.unit_of_work import UnitOfWork

CommandHandler = Callable[[UnitOfWork, dict[str, Any]], Awaitable[dict[str, Any]]]
_NAME = re.compile(r"[a-z][a-z0-9_]*")

COMMAND_SOURCE = "core"
COMMAND_APPENDER = "system"
COMMAND_FIELDS: tuple[FieldSpec, ...] = (
    FieldSpec("role", "str"),
    FieldSpec("request_id", "str"),
    FieldSpec("args", "object"),
    FieldSpec("result", "object"),
)


class CommandError(LedgerError):
    """A handler's refusal. It rolls the command back and reaches the caller with its code."""


def command_kind(name: str) -> str:
    return f"{COMMAND_SOURCE}.{name}"


@dataclass(frozen=True)
class Command:
    """A registered command: its name, typed arguments and handler.

    The handler runs inside the writer transaction with the unit of work and the validated
    arguments, and returns a JSON object: the command's result. An optional read-only preflight
    receives validated, authorized arguments outside the writer. Recorded replays skip preflight.
    """

    name: str
    args: tuple[FieldSpec, ...]
    handler: CommandHandler
    record_if: Callable[[dict[str, Any]], bool] | None = None
    lane_scoped: bool = False
    preflight: Callable[[dict[str, Any]], Awaitable[None]] | None = None

    def __post_init__(self) -> None:
        if not _NAME.fullmatch(self.name):
            raise ValueError(f"command name {self.name!r} must be lowercase letters, digits, _")
        if self.lane_scoped and FieldSpec("lane", "str") not in self.args:
            raise ValueError(f"lane-scoped command {self.name!r} requires a lane argument")


class CommandRegistry:
    """The one registry of commands. Registering one registers its ``core.<name>`` event kind."""

    def __init__(self, events: EventRegistry) -> None:
        self._events = events
        self._commands: dict[str, Command] = {}

    def register(self, command: Command, *, rules: tuple[WriterRule, ...] = ()) -> None:
        if command.name in self._commands:
            raise ValueError(f"command {command.name!r} is already registered")
        kind = command_kind(command.name)
        if self._events.get(kind) is not None:
            raise ValueError(f"event kind {kind!r} is already registered")
        self._events.writer_rules.declare(command.name, rules)
        self._events.register(EventKind(kind, COMMAND_FIELDS, frozenset({COMMAND_APPENDER})))
        self._commands[command.name] = command

    def get(self, name: str) -> Command | None:
        return self._commands.get(name)

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._commands))
