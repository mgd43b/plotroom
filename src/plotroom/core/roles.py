"""Roles (AD-8): who an input comes from. ``<role>:<request_id>`` is unambiguous because no role
contains more than one colon and a worker slug contains none."""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from plotroom.core.events import Event
    from plotroom.core.lanes import Lane
    from plotroom.core.next_items import Item

SIMPLE_TOKEN_ROLES = frozenset({"orchestrator", "clerk", "drain", "bridge"})
SIMPLE_ROLES = SIMPLE_TOKEN_ROLES | {"system"}
WORKER_FAMILY = "worker"
ROLE_FAMILIES = SIMPLE_ROLES | {WORKER_FAMILY}
_WORKER = re.compile(r"worker:([a-z0-9](?:[a-z0-9_-]*[a-z0-9])?)")


@dataclass(frozen=True)
class Identity:
    project_id: str
    role: str


def role_family(role: str) -> str:
    """The role family a registry names: ``worker:<slug>`` is ``worker``, the rest are themselves.

    Raises ``ValueError`` for anything outside the role grammar.
    """
    if role in SIMPLE_ROLES:
        return role
    if _WORKER.fullmatch(role):
        return WORKER_FAMILY
    raise ValueError(
        f"invalid role {role!r}: expected one of {sorted(SIMPLE_ROLES)} or worker:<slug>"
    )


def validate_token_role(role: str) -> None:
    """Validate a role that can be issued a credential."""
    if role_family(role) == "system":
        raise ValueError("system is an internal role and cannot have a token")


@dataclass(frozen=True, slots=True)
class OwnershipView:
    """The calling role and read access to events in the command's transaction."""

    role: str
    _lookup: Callable[[str, str, str], Awaitable[Event | None]] = field(repr=False)

    _item: Callable[[str], Awaitable[Item | None]] = field(repr=False)

    _lane: Callable[[str], Awaitable[Lane | None]] = field(repr=False)

    async def find_lane(self, lane_id: str) -> Lane | None:
        return await self._lane(lane_id)

    async def find_event(self, source: str, kind: str, upstream_id: str) -> Event | None:
        return await self._lookup(source, kind, upstream_id)

    async def find_item(self, item_id: str) -> Item | None:
        return await self._item(item_id)


type Ownership = Callable[[OwnershipView, dict[str, Any]], Awaitable[bool]]


@dataclass(frozen=True)
class WriterRule:
    """A role family allowed to write, optionally subject to a read-only ownership predicate."""

    family: str
    owns: Ownership | None = None

    def __post_init__(self) -> None:
        if self.family not in ROLE_FAMILIES:
            raise ValueError(f"unknown writer role family {self.family!r}")


class WriterRules:
    """Authorization rows for command names and fully qualified event kinds."""

    def __init__(self) -> None:
        self._rules: dict[str, dict[str, WriterRule]] = {}

    def declare(self, operation: str, rules: tuple[WriterRule, ...]) -> None:
        if operation in self._rules:
            raise ValueError(f"writer rules for {operation!r} are already declared")
        by_family = {rule.family: rule for rule in rules}
        if len(by_family) != len(rules):
            raise ValueError(f"writer rules for {operation!r} declare a role family twice")
        self._rules[operation] = by_family

    def rule_for(self, operation: str, family: str) -> WriterRule | None:
        return self._rules.get(operation, {}).get(family)
