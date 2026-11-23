"""Local trigger actions with a scripted relay and a Claude-shaped drain."""

import asyncio
import json
import sys
from collections.abc import AsyncGenerator, Mapping
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from drain_standin import DrainStandIn
from fakes import FixedClock
from plotroom.adapters.store_sqlite import SqliteStore
from plotroom.adapters.switchboard import Switchboard
from plotroom.app.config import load_config
from plotroom.app.runtime import ActionHandler, ProjectRuntime
from plotroom.core.actions import SWITCHBOARD_SEND
from plotroom.core.catalog import build_catalog
from plotroom.core.events import LedgerError, NewEvent
from plotroom.core.github import PULL_REQUESTS, ObservedPull, apply_pull_poll
from plotroom.core.ledger import Ledger
from plotroom.core.outbox import LEASE_DURATION, lease_request_id
from plotroom.core.trigger_actions import SEND, DeliveryRoute
from plotroom.core.unit_of_work import UnitOfWork

REPO = "sample/project"
TIMESTAMP = "2026-11-23T10:00:00.000000Z"
ROUTES = {
    "relay": DeliveryRoute("switchboard", "fixture-peer"),
    "desktop": DeliveryRoute("claude", "fixture-session"),
}


@asynccontextmanager
async def open_ledger(directory: Path, clock: FixedClock) -> AsyncGenerator[Ledger]:
    store = SqliteStore("demo", directory)
    await store.migrate()
    ledger = Ledger(
        store,
        build_catalog(frozenset(ROUTES), github_repository=REPO, delivery_routes=ROUTES),
        clock,
        github_repository=REPO,
        drain_enabled=True,
    )
    ledger.start()
    try:
        yield ledger
    finally:
        await ledger.stop()
        await store.close()


def runtime(
    ledger: Ledger, clock: FixedClock, handlers: Mapping[str, ActionHandler] | None = None
) -> ProjectRuntime:
    return ProjectRuntime(
        ledger, clock, tick_seconds=60, action_attempts=2, delivery_routes=ROUTES, handlers=handlers
    )


async def pull(ledger: Ledger, number: int, *, state: str = "merged") -> None:
    assert state in ("merged", "open")
    cursor = await ledger.fetch_cursor(REPO, PULL_REQUESTS)
    observed = ObservedPull(
        number,
        f"branch-{number}",
        "a" * 40,
        REPO,
        "merged" if state == "merged" else "open",
        TIMESTAMP,
        "fixture input",
    )
    assert await ledger.run(
        "system", lambda uow: apply_pull_poll(uow, REPO, cursor, (observed,), TIMESTAMP)
    )


async def trigger(ledger: Ledger, key: str, action: dict[str, Any]) -> str:
    result = await ledger.execute(
        "orchestrator",
        "trigger",
        key,
        {"predicate": {"pr_merged": {"number": 1}}, "action": action},
    )
    return str(result.result["id"])


async def drain(ledger: Ledger, command: str, key: str, **args: Any) -> dict[str, Any]:
    return (await ledger.execute("drain", command, key, args)).result


async def test_fired_send_uses_the_scripted_relay_and_the_same_action_key(tmp_path: Path) -> None:
    (tmp_path / "available").touch()
    (tmp_path / "acked").touch()
    clock = FixedClock()
    async with open_ledger(tmp_path, clock) as ledger:
        relay = Switchboard(
            ledger,
            "fixture-board",
            (sys.executable, str(Path(__file__).with_name("switchboard_child.py")), str(tmp_path)),
            {"fixture-peer": "relay"},
        )
        # The shared stand-in pauses the first native send. Pre-create its marker here to
        # exercise the successful relay response in this test.
        (tmp_path / "send-first").touch()
        relay_task = asyncio.create_task(relay.run())
        try:
            await trigger(ledger, "relay", {"send": {"worker": "relay", "text": "please check"}})
            await pull(ledger, 1)
            action = await ledger.pending_action()
            assert action is not None
            runner = runtime(ledger, clock, {SWITCHBOARD_SEND: relay.send})
            async with asyncio.timeout(10):
                assert await runner.run_one_action()
            await pull(ledger, 1)
            assert not await runner.run_one_action()
            outgoing = json.loads((tmp_path / "send-retry").read_text())
            assert outgoing["to"] == "fixture-peer"
            assert outgoing["idempotency_key"] == action.key
            assert json.loads(outgoing["body"]) == {
                "action_key": action.key,
                "body": "please check",
            }
        finally:
            relay_task.cancel()
            await asyncio.gather(relay_task, return_exceptions=True)


async def test_mark_and_archive_proposals_use_lane_rules_and_deduplicate(tmp_path: Path) -> None:
    clock = FixedClock()
    async with open_ledger(tmp_path, clock) as ledger:
        await ledger.execute(
            "orchestrator",
            "lane_start",
            "start",
            {
                "lane": "fixture-lane",
                "worker": "desktop",
                "branch": "branch-2",
                "work_items": [],
            },
        )
        await pull(ledger, 2, state="open")
        await pull(ledger, 1)
        runner = runtime(ledger, clock)
        await trigger(ledger, "ready", {"mark": {"lane": "fixture-lane", "state": "ready"}})
        assert await runner.run_one_action()
        board, _ = await ledger.snapshot_board("orchestrator")
        [lane] = board[3]
        assert (lane.state, lane.pr, lane.head_sha) == ("ready", 2, "a" * 40)
        await trigger(ledger, "active", {"mark": {"lane": "fixture-lane", "state": "active"}})
        proposal = {"propose_archive": {"lane": "fixture-lane"}}
        await trigger(ledger, "archive-1", proposal)
        await trigger(ledger, "archive-2", proposal)
        for _ in range(3):
            assert await runner.run_one_action()
        _, items, counts, _ = await ledger.read_board("orchestrator")
        assert counts == {"active": 1}
        [archive] = [item for item in items if item.kind == "archive_lane"]
        assert archive.subject_ref == "fixture-lane"
        assert [
            event.fields["state"]
            for event in await ledger.events_after(0)
            if event.kind == "core.lane_marked"
        ] == ["ready", "active"]
        await ledger.execute("orchestrator", "lane_archive", "approved", {"lane": "fixture-lane"})
        assert not any(
            item.kind == "archive_lane" for item in (await ledger.read_board("orchestrator"))[1]
        )
        for key, invalid in enumerate(
            [
                {"deploy": {}},
                {"mark": {"lane": "fixture-lane", "state": "archived"}},
                {"send": {"worker": "unconfigured", "text": "hello"}},
            ]
        ):
            with pytest.raises(LedgerError) as exc:
                await trigger(ledger, f"invalid-{key}", invalid)
            assert exc.value.code == "invalid"


async def test_drain_waits_leases_redelivers_and_completes_after_restart(tmp_path: Path) -> None:
    clock = FixedClock()
    stand_in = DrainStandIn()
    async with open_ledger(tmp_path, clock) as ledger:
        awaiting = asyncio.Event()

        async def report(_elapsed: float, _total: float) -> None:
            awaiting.set()

        cursor = await ledger.head_seq()
        waiter = asyncio.create_task(ledger.wait_for_item("drain", cursor, 10, report))
        try:
            async with asyncio.timeout(10):
                await awaiting.wait()
                await trigger(ledger, "desktop", {"send": {"worker": "desktop", "text": "work"}})
                await pull(ledger, 1)
                action = await ledger.pending_action()
                assert action is not None
                assert await runtime(ledger, clock).run_one_action()
                cursor, wake = await waiter
        finally:
            if not waiter.done():
                waiter.cancel()
                await asyncio.gather(waiter, return_exceptions=True)
        assert wake is not None
        assert wake == {"kind": "outbox_ready", "seq": wake["seq"]}
        assert cursor >= wake["seq"]
        for role in ("orchestrator", "clerk", "worker:desktop", "bridge"):
            for tool, args in (("outbox_lease", {}), ("outbox_done", {"act_id": "no"})):
                with pytest.raises(LedgerError) as exc:
                    await ledger.execute(role, tool, f"{role}:{tool}", args)
                assert exc.value.code == "forbidden"
        request_one = lease_request_id(stand_in.consumer_id, "shared-request-id")
        request_two = lease_request_id(str(uuid4()), "shared-request-id")
        one, two = await asyncio.gather(
            drain(ledger, "outbox_lease", request_one), drain(ledger, "outbox_lease", request_two)
        )
        [original] = [entry["lease"] for entry in (one, two) if entry["lease"] is not None]
        assert original["action_key"] == action.key
        assert original["session_id"] == "fixture-session"
        assert original["text"] == "work"
        stand_in.deliver(original)
        request_id = request_one if one["lease"] is not None else request_two
        assert (await drain(ledger, "outbox_lease", request_id))["lease"] == original
        assert (await ledger.wait_for_item("drain", cursor, 0, report))[1] is None

    async with open_ledger(tmp_path, clock) as ledger:
        assert (await drain(ledger, "outbox_lease", "unexpired"))["lease"] is None
        clock.advance(LEASE_DURATION.total_seconds())
        assert (await ledger.wait_for_item("drain", cursor, 0, report))[1] == wake
        redelivery = (await drain(ledger, "outbox_lease", "standin-retry"))["lease"]
        assert redelivery["act_id"] != original["act_id"]
        assert redelivery["action_key"] == original["action_key"]
        with pytest.raises(LedgerError, match="current outbox lease"):
            await drain(ledger, "outbox_done", "stale", act_id=original["act_id"])
        stand_in.deliver(redelivery)
        done = await drain(ledger, "outbox_done", "complete", act_id=redelivery["act_id"])
        assert done == {"action_key": action.key, "state": "done"}
        assert await drain(ledger, "outbox_done", "complete", act_id=redelivery["act_id"]) == done
        assert stand_in.deliveries == {action.key: ("fixture-session", "work")}
        assert (await drain(ledger, "outbox_lease", "after-done"))["lease"] is None
        assert not await runtime(ledger, clock).run_one_action()


async def test_send_dispatch_accepts_another_cause(tmp_path: Path) -> None:
    clock = FixedClock()
    async with open_ledger(tmp_path, clock) as ledger:

        async def enqueue(uow: UnitOfWork) -> str:
            cause = (await uow.append(NewEvent("core", "core.tick", "other-cause"))).event
            return await uow.enqueue_action(cause, SEND, {"worker": "desktop", "text": "stand-in"})

        key = await ledger.run("system", enqueue)
        assert await runtime(ledger, clock).run_one_action()
        assert (await drain(ledger, "outbox_lease", "standin"))["lease"]["action_key"] == key


async def test_lease_expiry_wakes_an_already_waiting_drain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = FixedClock()
    monkeypatch.setattr("plotroom.core.outbox.LEASE_DURATION", timedelta(milliseconds=100))
    async with open_ledger(tmp_path, clock) as ledger:
        await pull(ledger, 1)
        await trigger(ledger, "send", {"send": {"worker": "desktop", "text": "work"}})
        assert await runtime(ledger, clock).run_one_action()
        assert (await drain(ledger, "outbox_lease", "initial"))["lease"] is not None
        cursor = await ledger.head_seq()
        reports = 0

        async def report(_elapsed: float, _total: float) -> None:
            nonlocal reports
            reports += 1
            clock.advance(0.1)

        # The next progress deadline is five seconds away. Only the expiry
        # deadline can finish this wait within the test timeout.
        async with asyncio.timeout(2):
            position, wake = await ledger.wait_for_item("drain", cursor, 10, report)
        assert reports == 1
        assert wake is not None
        assert wake["kind"] == "outbox_ready"
        assert position == cursor == await ledger.head_seq()


def test_claude_delivery_config_is_independent_of_session_intake(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        '[projects.demo]\ndrain_session = "fixture-drain"\n'
        '[projects.demo.workers.desktop]\nclaude_session = "fixture-session"\n',
        encoding="utf-8",
    )
    config = load_config(path, {})
    assert config.session_readers == {}
    assert config.drain_sessions == {"demo": "fixture-drain"}
    assert config.delivery_routes == {"demo": {"desktop": ROUTES["desktop"]}}


def test_delivery_preserves_an_existing_dual_channel_worker_config(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        '[projects.demo]\ndrain_session = "fixture-drain"\n'
        '[projects.demo.switchboard]\nboard = "fixture-board"\n'
        '[projects.demo.workers.relay]\nswitchboard_address = "fixture-peer"\n'
        'claude_session = "fixture-session"\n',
        encoding="utf-8",
    )
    config = load_config(path, {})
    assert config.delivery_routes == {"demo": {"relay": ROUTES["relay"]}}
    path.write_text(
        path.read_text().replace('drain_session = "fixture-drain"', ""), encoding="utf-8"
    )
    assert load_config(path, {}).delivery_routes == config.delivery_routes
