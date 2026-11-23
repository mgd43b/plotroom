"""Replay: a recorded input set applied twice leaves every table and the ``seq`` counter as it was."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from fakes import Backend, FixedClock, Fixture, apply_inputs, load_inputs, open_backend
from plotroom.core.ledger import Ledger

BACKENDS = ["memory", "sqlite"]
# pr-104, turn-1, note-1 (+ its core event), the retry, the clerk's note (+ core event), the
# duplicate pr-104 and pr-105: which of the recorded inputs change the ledger on a first pass
FIRST_PASS = [True, True, True, False, True, False, True]


@pytest.fixture(params=BACKENDS)
async def backend(request: pytest.FixtureRequest, tmp_path: Path) -> AsyncIterator[Backend]:
    opened = await open_backend(request.param, tmp_path)
    try:
        yield opened
    finally:
        await opened.store.close()


async def test_replaying_the_recorded_inputs_twice_changes_nothing(backend: Backend) -> None:
    fixture = Fixture()
    ledger = Ledger(backend.store, fixture.catalog, FixedClock())
    ledger.start()
    try:
        inputs = load_inputs()
        assert await apply_inputs(ledger, inputs) == FIRST_PASS
        after_first = backend.dump()
        assert await ledger.head_seq() == 7
        calls = list(fixture.calls)
        assert calls == ["note-1", "note-2"]

        assert await apply_inputs(ledger, inputs) == [False] * len(inputs)
        assert backend.dump() == after_first  # every table, and the seq counter
        assert await ledger.head_seq() == 7
        assert fixture.calls == calls  # no handler ran again
    finally:
        await ledger.stop()


async def test_the_recorded_inputs_land_as_recorded(backend: Backend) -> None:
    ledger = Ledger(backend.store, Fixture().catalog, FixedClock())
    ledger.start()
    try:
        await apply_inputs(ledger, load_inputs())
        events = await ledger.events_after(0)
    finally:
        await ledger.stop()
    assert [(e.seq, e.kind, e.upstream_id) for e in events] == [
        (1, "github.pr_merged", "pr-104@merged"),
        (2, "claude.turn_end", "turn-1"),
        (3, "fixture.noted", "note-1"),
        (4, "core.record_note", "orchestrator:req-1"),
        (5, "fixture.noted", "note-2"),
        (6, "core.record_note", "clerk:req-1"),
        (7, "github.pr_merged", "pr-105@merged"),
    ]
    first, second = events[0], events[1]
    # fields and body are stored verbatim (the duplicate's other fields and body changed nothing)
    assert first.fields == {"pr": 104, "merged_by": "octocat"}
    assert first.body == "Merged by the gate. <b>Not parsed</b>"
    assert second.lane_id == "lane_demo"
    assert second.fields["meta"] == {"a": [1, 2, {"b": None}]}
    assert second.body == "Turn one finished.\nSecond line, with a unicode snowman: ☃"
    assert first.lane_id is None


async def test_every_event_has_a_unique_evt_id_and_an_observed_at(backend: Backend) -> None:
    ledger = Ledger(backend.store, Fixture().catalog, FixedClock())
    ledger.start()
    try:
        await apply_inputs(ledger, load_inputs())
        events = await ledger.events_after(0)
    finally:
        await ledger.stop()
    assert len({e.id for e in events}) == len(events)
    assert all(e.id.startswith("evt_") for e in events)
    assert {e.observed_at for e in events} == {"2026-11-22T12:00:00.000000Z"}
