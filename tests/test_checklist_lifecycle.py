"""Checklist completion and archive behavior on the normal ledger path."""

from pathlib import Path

import pytest

from fakes import FixedClock
from plotroom.core.checklists import CHECKLIST_EMPTY
from plotroom.core.events import LedgerError
from plotroom.core.ledger import Ledger
from plotroom.core.triggers import TRIGGER_FIRED
from test_trigger_actions import open_ledger, pull, runtime, trigger

LANE = "post-merge"
SHA = "a" * 40
STEPS = [
    {"step": "Inspect smoke", "owner": "desktop", "ref": "logs/smoke"},
    {"step": "Record evidence", "owner": "orchestrator", "ref": "tickets/result"},
]


async def prepare(ledger: Ledger, steps: list[dict[str, str]]) -> None:
    await ledger.execute(
        "orchestrator",
        "lane_start",
        "start",
        {"lane": LANE, "worker": "desktop", "branch": "branch-1", "work_items": []},
    )
    await pull(ledger, 1, state="open")
    await ledger.execute(
        "worker:desktop",
        "lane_ready",
        "ready",
        {"lane": LANE, "pr": 1, "head_sha": SHA, "checklist": steps},
    )


async def archive_ids(ledger: Ledger) -> list[str]:
    _, items, _, _ = await ledger.read_board("orchestrator")
    return [item.id for item in items if item.kind == "archive_lane" and item.subject_ref == LANE]


async def test_steps_guard_archive_and_share_the_proposal(tmp_path: Path) -> None:
    clock = FixedClock()
    async with open_ledger(tmp_path, clock) as ledger:
        await prepare(ledger, STEPS)
        first = {"lane": LANE, "step": STEPS[0]["step"], "ref": "fixture evidence"}
        with pytest.raises(LedgerError, match="no active checklist"):
            await ledger.execute("worker:desktop", "checklist_done", "before-merge", first)
        await pull(ledger, 1)
        assert await archive_ids(ledger) == []
        with pytest.raises(LedgerError, match=r"Inspect smoke.*Record evidence") as refusal:
            await ledger.execute("orchestrator", "lane_archive", "approved", {"lane": LANE})
        assert refusal.value.code == "conflict"
        with pytest.raises(LedgerError) as denial:
            await ledger.execute("worker:relay", "checklist_done", "elsewhere", first)
        assert denial.value.code == "forbidden"

        done = await ledger.execute("worker:desktop", "checklist_done", "first", first)
        assert done.result == {**STEPS[0], "completion_ref": "fixture evidence", "changed": True}
        duplicate = await ledger.execute("worker:desktop", "checklist_done", "first", first)
        assert duplicate.replayed
        assert duplicate.result == done.result
        repeated = await ledger.execute("worker:desktop", "checklist_done", "again", first)
        assert repeated.result == {**done.result, "changed": False}
        assert await archive_ids(ledger) == []
        with pytest.raises(LedgerError, match="Record evidence") as remaining:
            await ledger.execute("orchestrator", "lane_archive", "approved", {"lane": LANE})
        assert "Inspect smoke" not in remaining.value.message

        last = {"lane": LANE, "step": STEPS[1]["step"]}
        result = await ledger.execute("orchestrator", "checklist_done", "last", last)
        assert result.result == {**STEPS[1], "completion_ref": STEPS[1]["ref"], "changed": True}
        [proposal] = await archive_ids(ledger)
        await trigger(ledger, "same-proposal", {"propose_archive": {"lane": LANE}})
        assert await runtime(ledger, clock).run_one_action()
        assert await archive_ids(ledger) == [proposal]
        await ledger.execute("orchestrator", "lane_archive", "approved", {"lane": LANE})
        assert await archive_ids(ledger) == []


async def test_empty_ready_proposes_archive_only_when_merged(tmp_path: Path) -> None:
    async with open_ledger(tmp_path, FixedClock()) as ledger:
        await prepare(ledger, [])
        assert await archive_ids(ledger) == []
        await pull(ledger, 1)
        [proposal] = await archive_ids(ledger)
        await pull(ledger, 1)
        assert await archive_ids(ledger) == [proposal]
        await ledger.execute("orchestrator", "lane_archive", "archive-empty", {"lane": LANE})
        assert await archive_ids(ledger) == []
        assert not any(event.kind == CHECKLIST_EMPTY for event in await ledger.events_after(0))


async def test_checklist_empty_fires_once_across_restarts(tmp_path: Path) -> None:
    clock = FixedClock()
    predicate = {"checklist_empty": {"lane": LANE}}
    args = {
        "predicate": predicate,
        "action": {"notify_orchestrator": {"text": "Fixture checklist finished"}},
    }
    first = {"lane": LANE, "step": STEPS[0]["step"]}
    last = {"lane": LANE, "step": STEPS[1]["step"]}
    async with open_ledger(tmp_path, clock) as ledger:
        await prepare(ledger, STEPS)
        await ledger.execute("orchestrator", "trigger", "watch-empty", args)
        [pending] = await ledger.list_triggers("orchestrator", None)
        assert pending["state"] == "open"
        await pull(ledger, 1)
        await ledger.execute("worker:desktop", "checklist_done", "first", first)
        assert await ledger.list_triggers("orchestrator", None) == [pending]

    async with open_ledger(tmp_path, clock) as ledger:
        assert await ledger.list_triggers("orchestrator", None) == [pending]
        await ledger.execute("worker:desktop", "checklist_done", "last", last)
        [fired] = await ledger.list_triggers("orchestrator", None)
        assert (fired["id"], fired["state"]) == (pending["id"], "fired")
        [archive] = await archive_ids(ledger)
        action = await ledger.pending_action()
        assert action is not None
        assert action.key == fired["action_key"]
        [empty] = [event for event in await ledger.events_after(0) if event.kind == CHECKLIST_EMPTY]
        assert empty.fields == {"lane": LANE, "version": 1}
        assert empty.seq < fired["fired_seq"]

    async with open_ledger(tmp_path, clock) as ledger:
        assert await ledger.list_triggers("orchestrator", None) == [fired]
        repeated = await ledger.execute("worker:desktop", "checklist_done", "retry-last", last)
        assert repeated.result["changed"] is False
        assert await archive_ids(ledger) == [archive]
        runner = runtime(ledger, clock)
        assert await runner.run_one_action()
        assert not await runner.run_one_action()
        assert await ledger.list_triggers("orchestrator", None) == [fired]
        events = await ledger.events_after(0)
        assert [event.id for event in events if event.kind == CHECKLIST_EMPTY] == [empty.id]
        assert [event.fields["trigger_id"] for event in events if event.kind == TRIGGER_FIRED] == [
            fired["id"]
        ]
