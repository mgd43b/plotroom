import asyncio
import json
import sys
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from drain_standin import DrainStandIn
from fakes import FixedClock
from plotroom.adapters.store_sqlite import SqliteStore
from plotroom.adapters.switchboard import Switchboard
from plotroom.adapters.usage_fixture import FixtureUsageMeter
from plotroom.app.runtime import ProjectRuntime
from plotroom.app.serve import Daemon
from plotroom.core.actions import SWITCHBOARD_SEND, Action
from plotroom.core.budgets import BUDGET_THRESHOLD, record_budget_observations
from plotroom.core.catalog import build_catalog
from plotroom.core.events import LedgerError, NewEvent, format_time
from plotroom.core.ledger import Ledger
from plotroom.core.outbox import LEASE_DURATION
from plotroom.core.trigger_actions import SEND, DeliveryRoute
from plotroom.core.unit_of_work import UnitOfWork
from plotroom.ports.usage_meter import UsageObservation
from test_daemon import make_config, run_daemon, stop
from test_switchboard import marker as relay_marker

ORCHESTRATOR = "orchestrator"
WORKER = "worker:fixture"
BUDGET_ARGS = {
    "run": "run",
    "lane": "lane",
    "cap_usd": 0.1,
    "provider": "fixture",
    "key_scope": "staging",
}
STOP_ROUTES = {
    "fixture": DeliveryRoute("switchboard", "fixture-peer"),
    "desktop": DeliveryRoute("claude", "fixture-session"),
}


@asynccontextmanager
async def open_ledger(
    directory: Path,
    clock: FixedClock,
    project: str = "demo",
    *,
    with_stop_routes: bool = False,
) -> AsyncGenerator[Ledger]:
    store = SqliteStore(project, directory)
    await store.migrate()
    routes = STOP_ROUTES if with_stop_routes else {}
    ledger = Ledger(
        store,
        build_catalog(frozenset({"fixture", *routes}), delivery_routes=routes),
        clock,
        drain_enabled=with_stop_routes,
    )
    ledger.start()
    try:
        yield ledger
    finally:
        await ledger.stop()
        await store.close()


async def start_lane(ledger: Ledger, lane: str = "lane", worker: str = "fixture") -> None:
    await ledger.execute(
        ORCHESTRATOR,
        "lane_start",
        f"start-{lane}",
        {"lane": lane, "worker": worker, "branch": f"work-{lane}", "work_items": []},
    )


def fixture_relay(ledger: Ledger, directory: Path) -> Switchboard:
    return Switchboard(
        ledger,
        "fixture-board",
        (sys.executable, str(Path(__file__).with_name("switchboard_child.py")), str(directory)),
        {"fixture-peer": "fixture"},
    )


async def cap_lane(ledger: Ledger, clock: FixedClock, worker: str) -> str:
    await start_lane(ledger, worker=worker)
    opened = await ledger.execute(ORCHESTRATOR, "budget_open", "open", BUDGET_ARGS)
    readings = (UsageObservation("fixture", "run", 0.1, clock.now()),)
    await ledger.run("system", lambda uow: record_budget_observations(uow, readings))
    return f"budget:{opened.result['id']}:100"


async def test_cumulative_spend_warnings_and_replay(tmp_path: Path) -> None:
    clock = FixedClock()
    meter = FixtureUsageMeter()
    start = clock.now()
    async with open_ledger(tmp_path, clock) as ledger:
        await start_lane(ledger)
        lane_activity = await ledger.run("system", lambda uow: uow.lane_activity("lane"))
        opened = (await ledger.execute(ORCHESTRATOR, "budget_open", "open", BUDGET_ARGS)).result
        assert opened["as_of"] is None
        assert opened["spent_usd"] == 0
        assert await ledger.budget_status(ORCHESTRATOR, "run") == [opened]
        assert await ledger.budget_status(ORCHESTRATOR, "other") == []
        runtime = ProjectRuntime(
            ledger, clock, tick_seconds=60, action_attempts=2, usage_meter=meter
        )

        stamp = start + timedelta(seconds=10)
        half = UsageObservation("fixture", "run", 0.05, stamp)
        same_time = stamp.astimezone(timezone(timedelta(hours=-6)))
        meter.readings = (
            half,
            UsageObservation("fixture", "run", 0.08, same_time),
            UsageObservation("fixture", "run", 0.08, start),
            UsageObservation("different-meter", "run", 0.1, stamp + timedelta(seconds=1)),
            UsageObservation("fixture", "unknown-run", 0.1, stamp),
        )
        clock.advance(60)
        assert await runtime.tick_if_due()
        [half_item] = (await ledger.read_next(ORCHESTRATOR, 0, 100))[1]
        assert half_item.dedup_key == f"budget:{opened['id']}:50"
        assert half_item.context["as_of"] == format_time(stamp)
        assert await ledger.budget_status(ORCHESTRATOR, "run") == [
            {**opened, "spent_usd": 0.05, "as_of": format_time(stamp)}
        ]
        await ledger.execute(ORCHESTRATOR, "ack", "ack-half", {"item": half_item.id})

        eighty_time = start + timedelta(seconds=20)
        eighty = UsageObservation("fixture", "run", 0.08, eighty_time)
        meter.readings = (half, eighty, eighty)
        clock.advance(60)
        assert await runtime.tick_if_due()
        [eighty_item] = (await ledger.read_next(ORCHESTRATOR, 0, 100))[1]
        assert eighty_item.dedup_key == f"budget:{opened['id']}:80"
        assert eighty_item.context["spent_usd"] == 0.08
        await ledger.execute(ORCHESTRATOR, "ack", "ack-eighty", {"item": eighty_item.id})

        corrected = start + timedelta(seconds=30)
        meter.readings = (UsageObservation("fixture", "run", 0.02, corrected), eighty)
        clock.advance(60)
        assert await runtime.tick_if_due()
        assert await ledger.budget_status(ORCHESTRATOR) == [
            {**opened, "spent_usd": 0.02, "as_of": format_time(corrected)}
        ]
        assert await ledger.run("system", lambda uow: uow.lane_activity("lane")) == lane_activity

    async with open_ledger(tmp_path, clock) as restarted:
        runtime = ProjectRuntime(
            restarted, clock, tick_seconds=60, action_attempts=2, usage_meter=meter
        )
        new_time = start + timedelta(seconds=40)
        meter.readings = (eighty, UsageObservation("fixture", "run", 0.09, new_time))
        clock.advance(60)
        assert await runtime.tick_if_due()
        assert await restarted.budget_status(ORCHESTRATOR, "run") == [
            {**opened, "spent_usd": 0.09, "as_of": format_time(new_time)}
        ]
        assert (await restarted.read_next(ORCHESTRATOR, 0, 100))[1] == []
        assert [
            event.fields["percent"]
            for event in await restarted.events_after(0)
            if event.kind == BUDGET_THRESHOLD
        ] == [50, 80]
        retry = await restarted.execute(ORCHESTRATOR, "budget_open", "open", BUDGET_ARGS)
        assert retry.replayed
        assert retry.result == opened


async def test_usage_meter_timeout_preserves_budget_and_tick_progress(tmp_path: Path) -> None:
    class StallingMeter(FixtureUsageMeter):
        stalled = False

        async def observations(self) -> tuple[UsageObservation, ...]:
            if self.stalled:
                await asyncio.Event().wait()
            return await super().observations()

    clock = FixedClock()
    meter = StallingMeter((UsageObservation("fixture", "run", 0.05, clock.now()),))
    async with open_ledger(tmp_path, clock) as ledger:
        await start_lane(ledger)
        await ledger.execute(ORCHESTRATOR, "budget_open", "open", BUDGET_ARGS)
        runtime = ProjectRuntime(
            ledger, clock, tick_seconds=1, action_attempts=2, usage_meter=meter
        )
        clock.advance(1)
        assert await runtime.tick_if_due()
        status = await ledger.budget_status(ORCHESTRATOR, "run")
        items = (await ledger.read_next(ORCHESTRATOR, 0, 100))[1]
        assert status[0]["spent_usd"] == 0.05
        assert [item.context["percent"] for item in items] == [50]

        meter.stalled = True
        head = await ledger.head_seq()
        clock.advance(1)
        async with asyncio.timeout(5):
            assert await runtime.tick_if_due()
        [tick] = await ledger.events_after(head)
        assert tick.kind == "core.tick"
        assert tick.upstream_id == format_time(clock.now())
        assert await ledger.budget_status(ORCHESTRATOR, "run") == status
        assert (await ledger.read_next(ORCHESTRATOR, 0, 100))[1] == items

        meter.stalled = False
        meter.readings = (UsageObservation("fixture", "run", 0.08, clock.now()),)
        clock.advance(1)
        assert await runtime.tick_if_due()
        [recovered] = await ledger.budget_status(ORCHESTRATOR, "run")
        assert recovered["spent_usd"] == 0.08
        assert {
            item.context["percent"] for item in (await ledger.read_next(ORCHESTRATOR, 0, 100))[1]
        } == {50, 80}


async def test_budget_as_of_round_trips_years_below_1000(tmp_path: Path) -> None:
    clock = FixedClock()
    stamp = "0999-12-31T23:59:58.000000Z"
    later = "0999-12-31T23:59:59.000000Z"
    first = UsageObservation("fixture", "run", 0.05, datetime.fromisoformat(stamp))
    second = UsageObservation("fixture", "run", 0.08, datetime.fromisoformat(later))
    async with open_ledger(tmp_path, clock) as ledger:
        await start_lane(ledger)
        await ledger.execute(ORCHESTRATOR, "budget_open", "open", BUDGET_ARGS)
        await ledger.run("system", lambda uow: record_budget_observations(uow, (first,)))
        [initial] = await ledger.budget_status(ORCHESTRATOR, "run")
        assert initial["as_of"] == stamp
        await ledger.run("system", lambda uow: record_budget_observations(uow, (second,)))
        [updated] = await ledger.budget_status(ORCHESTRATOR, "run")
        assert updated["as_of"] == later
        assert updated["spent_usd"] == 0.08


async def test_budget_open_validates_without_recording_failed_calls(tmp_path: Path) -> None:
    async with open_ledger(tmp_path, FixedClock()) as ledger:
        await start_lane(ledger)
        before = await ledger.head_seq()
        invalid: list[tuple[dict[str, Any], str]] = [
            ({"cap_usd": amount}, "invalid")
            for amount in (0, -1, float("nan"), float("inf"), 10**400, True)
        ]
        invalid.extend(
            (
                ({"key_scope": "fixture key material"}, "invalid"),
                ({"credential": "fixture-secret"}, "invalid"),
                ({"provider": ""}, "invalid"),
                ({"run": "\0"}, "invalid"),
                ({"lane": "missing"}, "conflict"),
            )
        )
        for overrides, code in invalid:
            with pytest.raises(LedgerError) as caught:
                await ledger.execute(
                    ORCHESTRATOR, "budget_open", "invalid", BUDGET_ARGS | overrides
                )
            assert caught.value.code == code
        with pytest.raises(LedgerError, match="may not execute"):
            await ledger.execute(WORKER, "budget_open", "unauthorized", BUDGET_ARGS)
        with pytest.raises(LedgerError, match="only the orchestrator"):
            await ledger.budget_status(WORKER)
        assert await ledger.events_after(before) == []
        assert await ledger.budget_status(ORCHESTRATOR) == []

        await ledger.execute(ORCHESTRATOR, "budget_open", "open", BUDGET_ARGS)
        with pytest.raises(LedgerError, match="already has a budget"):
            await ledger.execute(
                ORCHESTRATOR, "budget_open", "duplicate-run", BUDGET_ARGS | {"provider": "another"}
            )
        await ledger.execute(ORCHESTRATOR, "lane_archive", "archive", {"lane": "lane"})
        with pytest.raises(LedgerError, match="requires a current lane"):
            await ledger.execute(
                ORCHESTRATOR, "budget_open", "archived", BUDGET_ARGS | {"run": "archived"}
            )


async def test_budget_spend_and_warnings_roll_back_together(tmp_path: Path) -> None:
    clock = FixedClock()
    reading = UsageObservation("fixture", "run", 0.1, clock.now())
    async with open_ledger(tmp_path, clock) as ledger:
        await start_lane(ledger)
        opened = (await ledger.execute(ORCHESTRATOR, "budget_open", "open", BUDGET_ARGS)).result
        head = await ledger.head_seq()

        async def reject_after_poll(uow: UnitOfWork) -> None:
            await record_budget_observations(uow, (reading,))
            raise RuntimeError("fixture writer failure")

        with pytest.raises(RuntimeError, match="fixture writer failure"):
            await ledger.run("system", reject_after_poll)
        assert await ledger.events_after(head) == []
        assert await ledger.budget_status(ORCHESTRATOR) == [opened]
        assert (await ledger.read_next(ORCHESTRATOR, 0, 100))[1] == []
        assert await ledger.pending_action() is None
        await ledger.run("system", lambda uow: record_budget_observations(uow, (reading,)))
        assert {
            item.context["percent"] for item in (await ledger.read_next(ORCHESTRATOR, 0, 100))[1]
        } == {50, 80, 100}
        assert await ledger.pending_action() is not None


async def test_daemon_usage_meter_only_updates_its_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = FixedClock()
    config = replace(
        make_config(tmp_path, "one", "two"),
        workers={"one": frozenset({"fixture"}), "two": frozenset({"fixture"})},
    )
    meter = FixtureUsageMeter((UsageObservation("fixture", "run", 0.06, clock.now()),))
    runtimes: dict[str, ProjectRuntime] = {}

    def collect(runtime: ProjectRuntime) -> None:
        runtimes[runtime.ledger.project_id] = runtime

    monkeypatch.setattr(ProjectRuntime, "start", collect)
    daemon = Daemon(config, clock=clock, usage_meters={"one": meter})
    task, _ = await run_daemon(daemon)
    try:
        for ledger in daemon.ledgers.values():
            await start_lane(ledger)
            await ledger.execute(ORCHESTRATOR, "budget_open", "open", BUDGET_ARGS)
        clock.advance(config.tick_seconds)
        for runtime in runtimes.values():
            assert await runtime.tick_if_due()
        [first] = await daemon.ledgers["one"].budget_status(ORCHESTRATOR)
        [second] = await daemon.ledgers["two"].budget_status(ORCHESTRATOR)
        assert first["spent_usd"] == 0.06
        assert second["spent_usd"] == 0
        assert second["as_of"] is None
    finally:
        await stop(daemon, task)


async def test_cap_stops_both_lanes_once_across_restart(tmp_path: Path) -> None:
    clock = FixedClock()
    meter = FixtureUsageMeter()
    stop_keys: dict[str, str] = {}
    async with open_ledger(tmp_path, clock, with_stop_routes=True) as ledger:
        for worker in STOP_ROUTES:
            await start_lane(ledger, worker, worker)
            opened = await ledger.execute(
                ORCHESTRATOR,
                "budget_open",
                f"open-{worker}",
                BUDGET_ARGS
                | {
                    "run": worker,
                    "lane": worker,
                },
            )
            stop_keys[worker] = f"budget:{opened.result['id']}:100"
        meter.readings = tuple(
            UsageObservation("fixture", worker, 0.1, clock.now()) for worker in STOP_ROUTES
        )
        runner = ProjectRuntime(
            ledger, clock, tick_seconds=60, action_attempts=2, usage_meter=meter
        )
        clock.advance(60)
        assert await runner.tick_if_due()
        pending = await ledger.pending_actions(after=0, skip_branch_updates=False)
        assert {entry.action.key: entry.action.payload for entry in pending} == {
            key: {"worker": worker, "lane": worker, "text": "stop"}
            for worker, key in stop_keys.items()
        }

    # The relay and the drain both operate only on temporary local fixture state.
    relay_dir = tmp_path / "relay"
    relay_dir.mkdir()
    for marker in ("available", "acked"):
        (relay_dir / marker).touch()
    drain = DrainStandIn()
    async with open_ledger(tmp_path, clock, with_stop_routes=True) as interrupted:
        relay = fixture_relay(interrupted, relay_dir)
        runner = ProjectRuntime(
            interrupted,
            clock,
            tick_seconds=60,
            action_attempts=2,
            handlers={SWITCHBOARD_SEND: relay.send},
            delivery_routes=STOP_ROUTES,
        )
        relay_task = asyncio.create_task(relay.run())
        send_task = asyncio.create_task(runner.run_one_action())
        try:
            await relay_marker(relay_dir, "send-first")
        finally:
            send_task.cancel()
            relay_task.cancel()
            await asyncio.gather(send_task, relay_task, return_exceptions=True)
        pending = await interrupted.pending_action()
        assert pending is not None
        assert pending.key == stop_keys["fixture"]
        assert pending.attempts == 0

    async with open_ledger(tmp_path, clock, with_stop_routes=True) as restarted:
        relay = fixture_relay(restarted, relay_dir)
        runner = ProjectRuntime(
            restarted,
            clock,
            tick_seconds=60,
            action_attempts=2,
            handlers={SWITCHBOARD_SEND: relay.send},
            delivery_routes=STOP_ROUTES,
        )
        relay_task = asyncio.create_task(relay.run())
        try:
            async with asyncio.timeout(10):
                assert await runner.run_one_action()
                assert await runner.run_one_action()
            assert not await runner.run_one_action()
        finally:
            relay_task.cancel()
            await asyncio.gather(relay_task, return_exceptions=True)
        lease = (await restarted.execute("drain", "outbox_lease", "fixture-lease", {})).result[
            "lease"
        ]
        assert lease is not None
        drain.deliver(lease)  # Simulate a lost completion after effective delivery.
        assert drain.deliveries == {stop_keys["desktop"]: ("fixture-session", "stop")}

    async with open_ledger(tmp_path, clock, with_stop_routes=True) as replayed:
        runner = ProjectRuntime(
            replayed,
            clock,
            tick_seconds=60,
            action_attempts=2,
            delivery_routes=STOP_ROUTES,
            usage_meter=meter,
        )
        assert (await replayed.execute("drain", "outbox_lease", "unexpired", {})).result[
            "lease"
        ] is None
        clock.advance(LEASE_DURATION.total_seconds())
        assert await runner.tick_if_due()  # The same cumulative observations are replayed.
        meter.readings = tuple(
            UsageObservation("fixture", worker, 0.11, clock.now()) for worker in STOP_ROUTES
        )
        clock.advance(60)
        assert await runner.tick_if_due()  # A new sample above the cap also stays deduplicated.
        assert not await runner.run_one_action()
        redelivery = (await replayed.execute("drain", "outbox_lease", "redelivery", {})).result[
            "lease"
        ]
        assert redelivery is not None
        assert redelivery["act_id"] != lease["act_id"]
        assert redelivery["action_key"] == lease["action_key"] == stop_keys["desktop"]
        drain.deliver(redelivery)
        await replayed.execute(
            "drain", "outbox_done", "fixture-done", {"act_id": redelivery["act_id"]}
        )
        assert drain.deliveries == {stop_keys["desktop"]: ("fixture-session", "stop")}
        assert (await replayed.execute("drain", "outbox_lease", "next", {})).result["lease"] is None
        assert sorted(
            item.dedup_key
            for item in (await replayed.read_next(ORCHESTRATOR, 0, 100))[1]
            if item.context.get("percent") == 100
        ) == sorted(stop_keys.values())
        assert sorted(
            event.fields["action_key"]
            for event in await replayed.events_after(0)
            if event.kind == "core.action_attempt"
        ) == sorted(stop_keys.values())

    first, retry = [
        json.loads(request)
        for request in (relay_dir / "send-requests.jsonl").read_text().splitlines()
    ]
    assert first == retry
    assert first["to"] == "fixture-peer"
    assert first["idempotency_key"] == stop_keys["fixture"]
    assert json.loads(first["body"]) == {"action_key": stop_keys["fixture"], "body": "stop"}
    assert json.loads((relay_dir / "send-deliveries.json").read_text()) == {
        stop_keys["fixture"]: first,
    }


@pytest.mark.parametrize("worker", tuple(STOP_ROUTES))
@pytest.mark.parametrize("cap_before_archive", [False, True])
async def test_archived_budget_lane_cannot_stop_a_reassigned_worker(
    tmp_path: Path, worker: str, cap_before_archive: bool
) -> None:
    clock = FixedClock()
    released = UsageObservation("fixture", "run", 0.1, clock.now())

    async def observe(uow: UnitOfWork) -> None:
        await record_budget_observations(uow, (released,))

    async with open_ledger(tmp_path, clock, with_stop_routes=True) as ledger:
        await start_lane(ledger, worker=worker)
        opened = await ledger.execute(ORCHESTRATOR, "budget_open", "open", BUDGET_ARGS)
        stop_key = f"budget:{opened.result['id']}:100"
        if cap_before_archive:
            await ledger.run("system", observe)
        await ledger.execute(ORCHESTRATOR, "lane_archive", "archive", {"lane": "lane"})
        await start_lane(ledger, "different-lane", worker)
        if not cap_before_archive:
            await ledger.run("system", observe)

    switchboard_sends: list[Action] = []

    async def relay_stand_in(action: Action) -> None:
        switchboard_sends.append(action)

    async with open_ledger(tmp_path, clock, with_stop_routes=True) as restarted:
        runner = ProjectRuntime(
            restarted,
            clock,
            tick_seconds=60,
            action_attempts=2,
            handlers={SWITCHBOARD_SEND: relay_stand_in},
            delivery_routes=STOP_ROUTES,
        )
        assert await runner.run_one_action() == cap_before_archive
        assert not await runner.run_one_action()
        assert switchboard_sends == []
        assert (await restarted.execute("drain", "outbox_lease", "no-stop", {})).result[
            "lease"
        ] is None
        assert [
            item.dedup_key
            for item in (await restarted.read_next(ORCHESTRATOR, 0, 100))[1]
            if item.context.get("percent") == 100
        ] == [stop_key]


async def test_budget_stop_key_requires_its_matching_cap_cause(tmp_path: Path) -> None:
    async def mismatched(uow: UnitOfWork) -> None:
        cause = (await uow.append(NewEvent("core", "core.tick", "wrong-cause"))).event
        await uow.enqueue_action(
            cause,
            SEND,
            {"lane": "lane", "worker": "fixture", "text": "stop"},
            stable_key="budget:bud_fixture:100",
        )

    async with open_ledger(tmp_path, FixedClock()) as ledger:
        with pytest.raises(LedgerError, match="stable action key"):
            await ledger.run("system", mismatched)


@pytest.mark.parametrize("lease_state", ["unleased", "active", "expired"])
async def test_staged_cap_stop_is_hidden_after_reassignment(
    tmp_path: Path, lease_state: str
) -> None:
    clock = FixedClock()

    async def progress(_elapsed: float, _cap: float) -> None:
        pass

    async with open_ledger(tmp_path, clock, with_stop_routes=True) as ledger:
        cap_key = await cap_lane(ledger, clock, "desktop")
        runner = ProjectRuntime(
            ledger, clock, tick_seconds=60, action_attempts=2, delivery_routes=STOP_ROUTES
        )
        assert await runner.run_one_action()
        _, ready = await ledger.wait_for_item("drain", None, 0, progress)
        assert ready is not None
        assert ready["kind"] == "outbox_ready"
        lease = None
        if lease_state != "unleased":
            lease = (await ledger.execute("drain", "outbox_lease", "prior", {})).result["lease"]
            assert lease is not None
            assert lease["action_key"] == cap_key

    async with open_ledger(tmp_path, clock, with_stop_routes=True) as restarted:
        await restarted.execute(ORCHESTRATOR, "lane_archive", "archive", {"lane": "lane"})
        await start_lane(restarted, "different-lane", "desktop")
        if lease_state == "expired":
            clock.advance(LEASE_DURATION.total_seconds() + 1)
        _, ready = await restarted.wait_for_item("drain", None, 0, progress)
        assert ready is None
        assert (await restarted.execute("drain", "outbox_lease", "no-stale-stop", {})).result[
            "lease"
        ] is None
        if lease is not None and lease_state == "active":
            done = await restarted.execute(
                "drain", "outbox_done", "prior-done", {"act_id": lease["act_id"]}
            )
            assert done.result == {"action_key": cap_key, "state": "done"}


async def test_relay_checks_lane_again_after_registration_wait(tmp_path: Path) -> None:
    clock = FixedClock()
    relay_dir = tmp_path / "relay"
    relay_dir.mkdir()
    (relay_dir / "available").touch()
    (relay_dir / "acked").touch()
    async with open_ledger(tmp_path, clock, with_stop_routes=True) as ledger:
        await cap_lane(ledger, clock, "fixture")
        relay = fixture_relay(ledger, relay_dir)
        waiting_for_relay = asyncio.Event()

        async def send(action: Action) -> None:
            waiting_for_relay.set()
            await relay.send(action)

        runner = ProjectRuntime(
            ledger,
            clock,
            tick_seconds=60,
            action_attempts=2,
            delivery_routes=STOP_ROUTES,
            handlers={SWITCHBOARD_SEND: send},
        )
        send_task = asyncio.create_task(runner.run_one_action())
        try:
            async with asyncio.timeout(8):
                await waiting_for_relay.wait()
                assert not send_task.done()
                await ledger.execute(ORCHESTRATOR, "lane_archive", "archive", {"lane": "lane"})
                await start_lane(ledger, "different-lane", "fixture")
                relay_task = asyncio.create_task(relay.run())
                try:
                    assert await send_task
                finally:
                    relay_task.cancel()
                    await asyncio.gather(relay_task, return_exceptions=True)
        finally:
            send_task.cancel()
            await asyncio.gather(send_task, return_exceptions=True)
        assert await ledger.pending_action() is None
        assert not (relay_dir / "send-requests.jsonl").exists()


async def test_live_cap_stop_waits_for_route_after_restart(tmp_path: Path) -> None:
    clock = FixedClock()
    async with open_ledger(tmp_path, clock, with_stop_routes=True) as ledger:
        cap_key = await cap_lane(ledger, clock, "desktop")
        runner = ProjectRuntime(ledger, clock, tick_seconds=60, action_attempts=1)
        initial_commit = ledger.commit_signal
        assert not await runner.run_one_action()
        assert not await runner.run_one_action()
        assert not initial_commit.is_set()
        pending = await ledger.pending_action()
        assert pending is not None
        assert pending.key == cap_key
        assert pending.attempts == 0

    async with open_ledger(tmp_path, clock, with_stop_routes=True) as restarted:
        runner = ProjectRuntime(
            restarted, clock, tick_seconds=60, action_attempts=1, delivery_routes=STOP_ROUTES
        )
        assert await runner.run_one_action()
        assert not await runner.run_one_action()
        lease = (await restarted.execute("drain", "outbox_lease", "recovered", {})).result["lease"]
        assert lease is not None
        assert lease["action_key"] == cap_key
        assert lease["text"] == "stop"
