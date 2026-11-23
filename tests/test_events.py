"""The event-kind registry and ``Ledger.append``: typed fields, appending roles, dedupe (AD-2)."""

from __future__ import annotations

import math
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from fakes import FixedClock, Fixture, MemoryStore, open_backend
from plotroom.core.events import (
    EventKind,
    EventRegistry,
    FieldSpec,
    LedgerError,
    NewEvent,
    format_time,
)
from plotroom.core.ledger import Ledger
from plotroom.core.roles import role_family

type Parts = tuple[Ledger, MemoryStore, FixedClock]


@pytest.fixture
async def parts() -> AsyncIterator[Parts]:
    store, clock = MemoryStore(), FixedClock()
    ledger = Ledger(store, Fixture().catalog, clock)
    ledger.start()
    try:
        yield ledger, store, clock
    finally:
        await ledger.stop()


def merged(upstream_id: str = "pr-1@merged", **fields: Any) -> NewEvent:
    return NewEvent("github", "github.pr_merged", upstream_id, fields or {"pr": 1})


# roles


@pytest.mark.parametrize(
    ("role", "family"),
    [
        ("orchestrator", "orchestrator"),
        ("clerk", "clerk"),
        ("drain", "drain"),
        ("bridge", "bridge"),
        ("system", "system"),
        ("worker:ada", "worker"),
        ("worker:a-1_b", "worker"),
    ],
)
def test_role_families(role: str, family: str) -> None:
    assert role_family(role) == family


@pytest.mark.parametrize(
    "role",
    [
        "",
        "Orchestrator",
        "worker",
        "worker:",
        "worker:Ada",
        "worker:a:b",
        "worker:-a",
        "admin",
        "system:x",
        "worker:ada\n",
        " clerk",
    ],
)
def test_roles_outside_the_grammar_are_rejected(role: str) -> None:
    with pytest.raises(ValueError, match="invalid role"):
        role_family(role)


# the registry


def test_a_kind_must_look_like_source_dot_name() -> None:
    for bad in ["pr_merged", "GitHub.pr", "github.", ".x", "a.b.c", "github.pr-merged"]:
        with pytest.raises(ValueError, match="must look like"):
            EventKind(bad, (), frozenset({"system"}))


def test_a_kind_needs_known_appenders_and_unique_fields() -> None:
    with pytest.raises(ValueError, match="appenders"):
        EventKind("a.b", (), frozenset())
    with pytest.raises(ValueError, match="appenders"):
        EventKind("a.b", (), frozenset({"root"}))
    with pytest.raises(ValueError, match="appenders"):
        EventKind("a.b", (), frozenset({"worker:ada"}))  # families, not roles
    with pytest.raises(ValueError, match="twice"):
        EventKind("a.b", (FieldSpec("x", "str"), FieldSpec("x", "int")), frozenset({"system"}))
    with pytest.raises(ValueError, match="unknown type"):
        FieldSpec("x", "float")  # pyright: ignore[reportArgumentType]


def test_registering_a_kind_twice_is_refused() -> None:
    registry = EventRegistry()
    kind = EventKind("a.b", (), frozenset({"system"}))
    registry.register(kind)
    with pytest.raises(ValueError, match="already registered"):
        registry.register(kind)
    assert registry.kinds == ("a.b",)
    assert registry.get("a.b") is kind
    assert registry.get("a.c") is None


def test_a_kind_names_its_source_by_its_prefix() -> None:
    assert EventKind("github.pr_merged", (), frozenset({"system"})).source == "github"


def test_time_is_utc_rfc_3339_with_z() -> None:
    assert format_time(datetime(2026, 11, 22, 12, 0, 1, 5000, tzinfo=UTC)) == (
        "2026-11-22T12:00:01.005000Z"
    )
    plus_two = timezone(timedelta(hours=2))
    assert format_time(datetime(2026, 11, 22, 14, 0, 0, tzinfo=plus_two)) == (
        "2026-11-22T12:00:00.000000Z"
    )
    with pytest.raises(ValueError, match="timezone-aware"):
        format_time(datetime(2026, 11, 22, 12, 0, 0))


# appending


async def test_a_new_event_is_stored_with_the_next_seq(parts: Parts) -> None:
    ledger, _, clock = parts
    clock.advance(30)
    result = await ledger.append(
        "system",
        NewEvent("github", "github.pr_merged", "pr-1@merged", {"pr": 1}, "body", "lane_x"),
    )
    assert result.created is True
    event = result.event
    assert event.seq == 1
    assert event.id.startswith("evt_")
    assert len(event.id) == len("evt_") + 36
    assert (event.source, event.kind) == ("github", "github.pr_merged")
    assert event.upstream_id == "pr-1@merged"
    assert (event.fields, event.body, event.lane_id) == ({"pr": 1}, "body", "lane_x")
    assert event.observed_at == "2026-11-22T12:00:30.000000Z"
    assert (await ledger.append("system", merged("pr-2@merged"))).event.seq == 2
    assert await ledger.head_seq() == 2
    assert [e.seq for e in await ledger.events_after(0)] == [1, 2]
    assert [e.seq for e in await ledger.events_after(1)] == [2]
    assert await ledger.events_after(2) == []


async def test_a_duplicate_returns_the_stored_event_and_consumes_no_seq(parts: Parts) -> None:
    ledger, store, clock = parts
    first = await ledger.append("system", merged())
    clock.advance(60)
    again = await ledger.append(
        "bridge",
        NewEvent("github", "github.pr_merged", "pr-1@merged", {"pr": 7}, "another body", "lane_y"),
    )
    assert again.created is False
    assert again.event == first.event
    assert await ledger.head_seq() == 1
    assert len(store.dump()["events"]) == 1
    assert (await ledger.append("system", merged("pr-2@merged"))).event.seq == 2  # contiguous


async def test_the_same_id_under_another_kind_or_source_is_a_separate_event() -> None:
    fixture = Fixture()
    fixture.catalog.events.register(
        EventKind("gitlab.pr_merged", (FieldSpec("pr", "int"),), frozenset({"system"}))
    )
    ledger = Ledger(MemoryStore(), fixture.catalog, FixedClock())
    ledger.start()
    try:
        first = await ledger.append("system", merged())
        other_kind = await ledger.append(
            "system", NewEvent("fixture", "fixture.noted", "pr-1@merged", {"text": "t"})
        )
        other_source = await ledger.append(
            "system", NewEvent("gitlab", "gitlab.pr_merged", "pr-1@merged", {"pr": 1})
        )
        assert [r.created for r in (first, other_kind, other_source)] == [True, True, True]
        assert [r.event.seq for r in (first, other_kind, other_source)] == [1, 2, 3]
        assert len({r.event.upstream_id for r in (first, other_kind, other_source)}) == 1
    finally:
        await ledger.stop()


def refused(role: str, event: NewEvent, code: str) -> Any:
    return pytest.param(role, event, code, id=f"{role}-{event.kind}-{code}-{len(str(event))}")


REFUSED = [
    refused("system", NewEvent("github", "github.nope", "x", {}), "invalid"),
    refused("system", NewEvent("github", "nokind", "x", {}), "invalid"),
    refused("system", NewEvent("gitlab", "github.pr_merged", "x", {"pr": 1}), "invalid"),
    refused("system", NewEvent("github", "github.pr_merged", "", {"pr": 1}), "invalid"),
    refused("system", NewEvent("github", "github.pr_merged", "x", {}), "invalid"),
    refused("system", NewEvent("github", "github.pr_merged", "x", {"pr": 1, "x": 1}), "invalid"),
    refused("system", NewEvent("github", "github.pr_merged", "x", {"pr": "1"}), "invalid"),
    refused("system", NewEvent("github", "github.pr_merged", "x", {"pr": True}), "invalid"),
    refused("system", NewEvent("github", "github.pr_merged", "x", {"pr": 1.5}), "invalid"),
    refused(
        "system", NewEvent("github", "github.pr_merged", "x", {"pr": 1, "merged_by": 3}), "invalid"
    ),
    refused("system", NewEvent("github", "github.pr_merged", "x", {"pr": 1}, body=5), "invalid"),  # pyright: ignore[reportArgumentType]
    refused(
        "system", NewEvent("github", "github.pr_merged", "x", {"pr": 1}, lane_id=""), "invalid"
    ),
    refused("system", NewEvent("github", "github.pr_merged", "x", [1]), "invalid"),  # pyright: ignore[reportArgumentType]
    refused("orchestrator", NewEvent("github", "github.pr_merged", "x", {"pr": 1}), "forbidden"),
    refused("worker:ada", NewEvent("github", "github.pr_merged", "x", {"pr": 1}), "forbidden"),
    refused("clerk", NewEvent("claude", "claude.turn_end", "x", {"turns": 1}), "forbidden"),
    refused("root", NewEvent("github", "github.pr_merged", "x", {"pr": 1}), "invalid"),
    refused("worker", NewEvent("claude", "claude.turn_end", "x", {"turns": 1}), "invalid"),
    # core events are appended by core, as system, never by a caller's own role
    refused("orchestrator", NewEvent("core", "core.record_note", "x", {}), "forbidden"),
]


@pytest.mark.parametrize(("role", "event", "code"), REFUSED)
async def test_a_refused_append_writes_nothing(
    parts: Parts, role: str, event: NewEvent, code: str
) -> None:
    ledger, store, _ = parts
    await ledger.append("system", merged("seed"))  # so "unchanged" is not just "empty"
    before = store.dump()
    with pytest.raises(LedgerError) as raised:
        await ledger.append(role, event)
    assert raised.value.code == code
    assert store.dump() == before
    assert await ledger.head_seq() == 1


async def test_a_worker_appends_what_its_family_may(parts: Parts) -> None:
    ledger, _, _ = parts
    result = await ledger.append(
        "worker:ada", NewEvent("claude", "claude.turn_end", "t1", {"turns": 2})
    )
    assert result.created is True


async def test_every_field_type_is_checked_and_bool_is_not_a_number(parts: Parts) -> None:
    ledger, _, _ = parts
    good = NewEvent(
        "claude",
        "claude.turn_end",
        "good",
        {"turns": 1, "ratio": 1, "done": False, "meta": {"k": [1, "a", None, 2.5]}, "tags": []},
    )
    assert (await ledger.append("orchestrator", good)).created is True
    bad_fields: list[dict[str, Any]] = [
        {"ratio": True},
        {"ratio": "1"},
        {"ratio": math.nan},
        {"ratio": math.inf},
        {"done": 0},
        {"done": "yes"},
        {"meta": []},
        {"meta": {1: "non-string key"}},
        {"meta": {"k": {1, 2}}},
        {"meta": {"k": (1, 2)}},
        {"meta": {"k": math.nan}},
        {"tags": {}},
        {"tags": (1,)},
        {"tags": [object()]},
    ]
    for index, bad in enumerate(bad_fields):
        event = NewEvent("claude", "claude.turn_end", f"bad-{index}", {"turns": 1, **bad})
        with pytest.raises(LedgerError) as raised:
            await ledger.append("orchestrator", event)
        assert raised.value.code == "invalid", bad
    assert await ledger.head_seq() == 1


def test_ledger_error_codes_come_from_the_envelope() -> None:
    assert LedgerError("forbidden", "no").code == "forbidden"
    with pytest.raises(ValueError, match="unknown error code"):
        LedgerError("teapot", "no")  # pyright: ignore[reportArgumentType]


@pytest.mark.parametrize("name", ["memory", "sqlite"])
async def test_a_duplicate_is_returned_whatever_its_fields_and_a_new_bad_event_is_refused(
    name: str, tmp_path: Path
) -> None:
    backend = await open_backend(name, tmp_path)
    ledger = Ledger(backend.store, Fixture().catalog, FixedClock())
    ledger.start()
    try:
        first = await ledger.append("system", merged("pr-1@merged", pr=1))
        before = backend.dump()
        for fields in ({}, {"pr": "one"}, {"pr": True}, {"pr": 1, "surprise": 2}):
            again = await ledger.append(
                "system", NewEvent("github", "github.pr_merged", "pr-1@merged", fields, "other")
            )
            assert (again.created, again.event) == (False, first.event)
        assert backend.dump() == before
        assert await ledger.head_seq() == 1
        # the same bad fields on a new identity are still refused, and take no seq
        for fields in ({}, {"pr": "one"}, {"pr": 1, "surprise": 2}):
            with pytest.raises(LedgerError) as raised:
                await ledger.append(
                    "system", NewEvent("github", "github.pr_merged", "pr-2@merged", fields)
                )
            assert raised.value.code == "invalid"
        assert backend.dump() == before
        # identity errors still refuse a duplicate
        with pytest.raises(LedgerError) as forbidden:
            await ledger.append("clerk", merged("pr-1@merged"))
        assert forbidden.value.code == "forbidden"
    finally:
        await ledger.stop()
        await backend.store.close()


async def test_an_integer_too_large_for_a_float_is_still_a_number(parts: Parts) -> None:
    ledger, _, _ = parts
    huge = NewEvent("claude", "claude.turn_end", "huge", {"turns": 1, "ratio": 10**400})
    assert (await ledger.append("orchestrator", huge)).created is True  # not an OverflowError


async def test_fields_changed_by_the_caller_while_the_store_is_awaited_are_not_stored(
    parts: Parts,
) -> None:
    ledger, store, _ = parts
    fields: dict[str, Any] = {"pr": 7}

    def another_task_runs() -> None:
        fields["pr"] = "not an int"  # would not have passed validation
        fields["surprise"] = 1

    store.on_allocate = another_task_runs
    appended = await ledger.append("system", NewEvent("github", "github.pr_merged", "pr-7", fields))
    assert appended.event.fields == {"pr": 7}
    assert (await ledger.events_after(0))[0].fields == {"pr": 7}
