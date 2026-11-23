"""Typed events and the event-kind registry (AD-2).

Every input becomes one event ``(seq, id, source, kind, upstream_id, lane_id?, fields, body,
observed_at)``. The registry declares, per kind, its typed ``fields`` and the role families that
may append it; ``validate_append`` is the single check.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal, get_args

from plotroom.core.envelope import ERROR_CODES, ErrorCode
from plotroom.core.roles import ROLE_FAMILIES, WriterRule, WriterRules, role_family

FieldType = Literal["str", "int", "bool", "number", "object", "array"]
FIELD_TYPES: tuple[str, ...] = get_args(FieldType)
_KIND = re.compile(r"[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*")


class LedgerError(Exception):
    """A refused input. ``code`` is one of the envelope's error codes."""

    def __init__(self, code: ErrorCode, message: str) -> None:
        if code not in ERROR_CODES:
            raise ValueError(f"unknown error code {code!r}; expected one of {ERROR_CODES}")
        super().__init__(message)
        self.code: ErrorCode = code
        self.message = message


def format_time(moment: datetime) -> str:
    """UTC, RFC 3339 with ``Z`` and fixed microsecond precision, so text order is time order."""
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError("a clock must return a timezone-aware datetime")
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


@dataclass(frozen=True)
class FieldSpec:
    """One declared field of an event kind (or one argument of a command)."""

    name: str
    type: FieldType
    required: bool = True

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("a field needs a name")
        if self.type not in FIELD_TYPES:
            raise ValueError(f"field {self.name!r}: unknown type {self.type!r}")


@dataclass(frozen=True)
class NewEvent:
    """An input not yet stored: no ``seq``, ``id`` or ``observed_at`` (the ledger assigns them)."""

    source: str
    kind: str
    upstream_id: str
    fields: dict[str, Any] = field(default_factory=lambda: {})
    body: str = ""
    lane_id: str | None = None


@dataclass(frozen=True)
class Event:
    """A stored event."""

    seq: int
    id: str
    source: str
    kind: str
    upstream_id: str
    lane_id: str | None
    fields: dict[str, Any]
    body: str
    observed_at: str


@dataclass(frozen=True)
class AppendResult:
    """The event now stored for the appended identity; ``created`` is false for a duplicate."""

    event: Event
    created: bool


def is_json(value: Any) -> bool:
    """Whether ``value`` is plain JSON: dicts with string keys, lists, strings, finite numbers."""
    if value is None or isinstance(value, (str, bool, int)):
        return True
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, list):
        return all(is_json(item) for item in value)  # pyright: ignore[reportUnknownVariableType]
    if isinstance(value, dict):
        return all(
            isinstance(key, str) and is_json(item)
            for key, item in value.items()  # pyright: ignore[reportUnknownVariableType]
        )
    return False


def _has_type(value: Any, expected: str) -> bool:
    # bool is a subclass of int; a flag is never an integer or a number here
    if isinstance(value, bool):
        return expected == "bool"
    if expected == "str":
        return isinstance(value, str)
    if expected == "int":
        return isinstance(value, int)
    if expected == "number":
        # an int of any size is a number; isfinite() would overflow converting it to a float
        return isinstance(value, int) or (isinstance(value, float) and math.isfinite(value))
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    return False


def validate_fields(specs: tuple[FieldSpec, ...], values: dict[str, Any], what: str) -> None:
    """Refuse missing, unknown and mistyped fields, and values that are not JSON."""
    declared = {spec.name: spec for spec in specs}
    for name in values:
        if name not in declared:
            raise LedgerError("invalid", f"{what}: unknown field {name!r}")
    for spec in specs:
        if spec.name not in values:
            if spec.required:
                raise LedgerError("invalid", f"{what}: missing required field {spec.name!r}")
            continue
        value = values[spec.name]
        if not _has_type(value, spec.type):
            raise LedgerError("invalid", f"{what}: field {spec.name!r} must be of type {spec.type}")
        if not is_json(value):
            raise LedgerError("invalid", f"{what}: field {spec.name!r} must be JSON")


@dataclass(frozen=True)
class EventKind:
    """A registered kind: ``<source>.<name>``, its fields and the role families that append it."""

    kind: str
    fields: tuple[FieldSpec, ...]
    appenders: frozenset[str]

    def __post_init__(self) -> None:
        if not _KIND.fullmatch(self.kind):
            raise ValueError(f"event kind {self.kind!r} must look like <source>.<name>")
        names = [spec.name for spec in self.fields]
        if len(set(names)) != len(names):
            raise ValueError(f"event kind {self.kind!r} declares a field twice")
        unknown = self.appenders - ROLE_FAMILIES
        if unknown or not self.appenders:
            raise ValueError(
                f"event kind {self.kind!r}: appenders must be a non-empty subset of "
                f"{sorted(ROLE_FAMILIES)}, got {sorted(self.appenders)}"
            )

    @property
    def source(self) -> str:
        """The upstream system the kind belongs to: its prefix."""
        return self.kind.partition(".")[0]


class EventRegistry:
    """The one registry of event kinds."""

    def __init__(self) -> None:
        self._kinds: dict[str, EventKind] = {}
        self.writer_rules = WriterRules()

    def register(self, kind: EventKind) -> None:
        if kind.kind in self._kinds:
            raise ValueError(f"event kind {kind.kind!r} is already registered")
        self.writer_rules.declare(kind.kind, tuple(WriterRule(role) for role in kind.appenders))
        self._kinds[kind.kind] = kind

    def get(self, kind: str) -> EventKind | None:
        return self._kinds.get(kind)

    @property
    def kinds(self) -> tuple[str, ...]:
        return tuple(sorted(self._kinds))

    def validate_identity(self, role: str, event: NewEvent) -> EventKind:
        """The registered kind when ``role`` may append ``event``'s identity; else ``LedgerError``.

        Checks everything that names the event, not its declared fields: ``invalid`` for a
        malformed role, an unregistered kind, a ``source`` that is not the kind's prefix, a bad
        ``upstream_id``, ``body``, ``lane_id`` or a ``fields`` that is not an object;
        ``forbidden`` when the role's family is not an appender.
        """
        try:
            family = role_family(role)
        except ValueError as exc:
            raise LedgerError("invalid", str(exc)) from None
        registered = self._kinds.get(event.kind)
        if registered is None:
            raise LedgerError("invalid", f"unknown event kind {event.kind!r}")
        if event.source != registered.source:
            raise LedgerError(
                "invalid",
                f"{event.kind}: source must be {registered.source!r}, got {event.source!r}",
            )
        if self.writer_rules.rule_for(event.kind, family) is None:
            raise LedgerError("forbidden", f"role {role!r} may not append {event.kind}")
        if not isinstance(event.upstream_id, str) or not event.upstream_id:  # pyright: ignore[reportUnnecessaryIsInstance]
            raise LedgerError("invalid", f"{event.kind}: upstream_id must be a non-empty string")
        if not isinstance(event.body, str):  # pyright: ignore[reportUnnecessaryIsInstance]
            raise LedgerError("invalid", f"{event.kind}: body must be a string")
        if event.lane_id is not None and (not isinstance(event.lane_id, str) or not event.lane_id):  # pyright: ignore[reportUnnecessaryIsInstance]
            raise LedgerError("invalid", f"{event.kind}: lane_id must be a non-empty string")
        if not isinstance(event.fields, dict):  # pyright: ignore[reportUnnecessaryIsInstance]
            raise LedgerError("invalid", f"{event.kind}: fields must be an object")
        return registered

    def validate_append(self, role: str, event: NewEvent) -> EventKind:
        """``validate_identity`` and then the per-field check: what a new event must pass."""
        registered = self.validate_identity(role, event)
        validate_fields(registered.fields, event.fields, event.kind)
        return registered
