"""Mutating commands: ``request_id`` deduplication and the ``core.<command>`` event (AD-2, AD-14)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest

from fakes import FixedClock, Fixture, MemoryStore
from plotroom.core.commands import Command, CommandError, CommandRegistry
from plotroom.core.events import EventRegistry, FieldSpec, LedgerError, NewEvent
from plotroom.core.ledger import Ledger
from plotroom.core.unit_of_work import UnitOfWork

type Parts = tuple[Ledger, MemoryStore, Fixture, FixedClock]


@pytest.fixture
async def parts() -> AsyncIterator[Parts]:
    store, fixture, clock = MemoryStore(), Fixture(), FixedClock()
    ledger = Ledger(store, fixture.catalog, clock)
    ledger.start()
    try:
        yield ledger, store, fixture, clock
    finally:
        await ledger.stop()


NOTE = {"key": "n1", "text": "hello"}


async def test_the_first_call_runs_the_handler_and_records_a_core_event(parts: Parts) -> None:
    ledger, _, fixture, clock = parts
    clock.advance(5)
    done = await ledger.execute("orchestrator", "record_note", "req-1", NOTE)
    assert done.replayed is False
    assert done.result == {"key": "n1", "seq": 1, "by": "orchestrator"}
    assert fixture.calls == ["n1"]
    events = await ledger.events_after(0)
    assert [(e.seq, e.kind) for e in events] == [(1, "fixture.noted"), (2, "core.record_note")]
    recorded = events[1]
    assert (recorded.source, recorded.upstream_id) == ("core", "orchestrator:req-1")
    assert recorded.fields == {
        "role": "orchestrator",
        "request_id": "req-1",
        "args": NOTE,
        "result": done.result,
    }
    assert recorded.observed_at == "2026-11-22T12:00:05.000000Z"
    assert await ledger.head_seq() == 2


async def test_a_retry_returns_the_first_result_and_runs_nothing(parts: Parts) -> None:
    ledger, store, fixture, _ = parts
    first = await ledger.execute("orchestrator", "record_note", "req-1", NOTE)
    before = store.dump()
    retried = await ledger.execute("orchestrator", "record_note", "req-1", NOTE)
    assert retried.replayed is True
    assert retried.result == first.result
    assert fixture.calls == ["n1"]
    assert store.dump() == before
    # a duplicate returns the original: the retry's arguments are not compared
    other_args = {"key": "n9", "text": "different"}
    again = await ledger.execute("orchestrator", "record_note", "req-1", other_args)
    assert (again.replayed, again.result) == (True, first.result)
    assert fixture.calls == ["n1"]
    assert store.dump() == before


async def test_the_role_is_part_of_the_key(parts: Parts) -> None:
    ledger, _, fixture, _ = parts
    await ledger.execute("orchestrator", "record_note", "req-1", NOTE)
    second = await ledger.execute("clerk", "record_note", "req-1", {"key": "n2", "text": "x"})
    assert second.replayed is False
    assert second.result["by"] == "clerk"
    assert fixture.calls == ["n1", "n2"]
    # a worker is keyed by its own name, not just its family
    ada = await ledger.execute("worker:ada", "record_note", "req-1", {"key": "n3", "text": "x"})
    bob = await ledger.execute("worker:bob", "record_note", "req-1", {"key": "n4", "text": "x"})
    assert (ada.replayed, bob.replayed) == (False, False)
    upstream_ids = {
        e.upstream_id for e in await ledger.events_after(0) if e.kind.startswith("core.")
    }
    assert upstream_ids == {
        "orchestrator:req-1",
        "clerk:req-1",
        "worker:ada:req-1",
        "worker:bob:req-1",
    }


async def test_another_request_id_or_command_runs_again(parts: Parts) -> None:
    ledger, _, fixture, _ = parts
    await ledger.execute("clerk", "record_note", "req-1", NOTE)
    assert (
        await ledger.execute("clerk", "record_note", "req-2", {"key": "n2", "text": "t"})
    ).replayed is False
    with pytest.raises(CommandError):
        await ledger.execute("clerk", "explode", "req-1", {"mode": "command_error"})
    assert fixture.calls == ["n1", "n2", "explode"]


async def test_a_failing_handler_leaves_nothing_behind_and_a_retry_runs_it_again(
    parts: Parts,
) -> None:
    ledger, store, fixture, _ = parts
    await ledger.execute("clerk", "record_note", "req-0", NOTE)
    before = store.dump()
    with pytest.raises(CommandError) as raised:
        await ledger.execute("orchestrator", "explode", "req-1", {"mode": "command_error"})
    assert (raised.value.code, raised.value.message) == ("conflict", "refused by the handler")
    assert isinstance(raised.value, LedgerError)
    assert store.dump() == before  # no handler event, no core event, no seq
    with pytest.raises(RuntimeError, match="the handler broke"):
        await ledger.execute("orchestrator", "explode", "req-1", {"mode": "crash"})
    assert store.dump() == before
    assert await ledger.head_seq() == 2
    assert fixture.calls == ["n1", "explode", "explode"]
    # the writer carries on
    assert (
        await ledger.execute("clerk", "record_note", "req-1", {"key": "n5", "text": "t"})
    ).result["seq"] == 3


async def test_a_handler_may_not_return_anything_but_a_json_object(parts: Parts) -> None:
    ledger, store, fixture, _ = parts

    async def bad(uow: UnitOfWork, args: dict[str, Any]) -> dict[str, Any]:
        await uow.append(NewEvent("fixture", "fixture.noted", "k", {"text": "t"}))
        return {"when": object()}

    fixture.catalog.commands.register(Command("bad_result", (), bad))
    with pytest.raises(TypeError, match="JSON object"):
        await ledger.execute("clerk", "bad_result", "r", {})
    assert store.dump() == {"events": [], "project_seq": 0}


async def test_a_handler_works_on_its_own_copy_of_the_arguments(parts: Parts) -> None:
    ledger, _, fixture, _ = parts

    async def scribble(uow: UnitOfWork, args: dict[str, Any]) -> dict[str, Any]:
        args["items"].append("added by the handler")
        return {"n": len(args["items"])}

    fixture.catalog.commands.register(Command("scribble", (FieldSpec("items", "array"),), scribble))
    sent: dict[str, Any] = {"items": ["a"]}
    await ledger.execute("clerk", "scribble", "r", sent)
    assert sent == {"items": ["a"]}
    recorded = (await ledger.events_after(0))[0]
    assert recorded.fields["args"] == {"items": ["a"]}
    assert recorded.fields["result"] == {"n": 2}


@pytest.mark.parametrize(
    ("role", "command", "request_id", "args"),
    [
        ("clerk", "no_such_command", "r", {}),
        ("clerk", "record_note", "", NOTE),
        ("clerk", "record_note", "r", {"key": "k"}),
        ("clerk", "record_note", "r", {"key": "k", "text": 1}),
        ("clerk", "record_note", "r", {"key": "k", "text": "t", "more": 1}),
        ("nobody", "record_note", "r", NOTE),
    ],
)
async def test_a_malformed_command_is_refused_and_writes_nothing(
    parts: Parts, role: str, command: str, request_id: str, args: dict[str, Any]
) -> None:
    ledger, store, fixture, _ = parts
    with pytest.raises(LedgerError) as raised:
        await ledger.execute(role, command, request_id, args)
    assert raised.value.code == "invalid"
    assert store.dump() == {"events": [], "project_seq": 0}
    assert fixture.calls == []


async def test_a_core_event_cannot_be_appended_as_an_ordinary_input(parts: Parts) -> None:
    ledger, store, _, _ = parts
    forged = NewEvent("core", "core.record_note", "clerk:req-1", {})
    for role in ("clerk", "orchestrator", "worker:ada", "bridge", "drain"):
        with pytest.raises(LedgerError) as raised:
            await ledger.append(role, forged)
        assert raised.value.code == "forbidden"
    assert store.dump()["events"] == []


def test_registering_a_command_registers_its_core_kind() -> None:
    events = EventRegistry()
    commands = CommandRegistry(events)

    async def handler(uow: UnitOfWork, args: dict[str, Any]) -> dict[str, Any]:
        return {}

    commands.register(Command("lane_start", (), handler))
    kind = events.get("core.lane_start")
    assert kind is not None
    assert kind.appenders == frozenset({"system"})
    assert [(f.name, f.type) for f in kind.fields] == [
        ("role", "str"),
        ("request_id", "str"),
        ("args", "object"),
        ("result", "object"),
    ]
    assert commands.names == ("lane_start",)
    with pytest.raises(ValueError, match="already registered"):
        commands.register(Command("lane_start", (), handler))
    for bad in ["", "Lane", "lane-start", "1lane", "a.b"]:
        with pytest.raises(ValueError, match="command name"):
            Command(bad, (), handler)


async def test_a_retry_returns_the_first_result_even_if_its_args_no_longer_validate(
    parts: Parts,
) -> None:
    ledger, store, fixture, _ = parts
    first = await ledger.execute("orchestrator", "record_note", "req-1", NOTE)
    before = store.dump()
    for bad in ({}, {"key": 1, "text": "t"}, {"key": "k", "text": "t", "more": 1}):
        retried = await ledger.execute("orchestrator", "record_note", "req-1", bad)
        assert (retried.replayed, retried.result) == (True, first.result)
    assert fixture.calls == ["n1"]
    assert store.dump() == before
    # a first call with invalid args is still refused and records nothing
    with pytest.raises(LedgerError) as raised:
        await ledger.execute("orchestrator", "record_note", "req-2", {"key": "k"})
    assert raised.value.code == "invalid"
    assert store.dump() == before
    assert fixture.calls == ["n1"]
