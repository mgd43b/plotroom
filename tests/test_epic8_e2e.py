"""Epic 8 acceptance through a local daemon and deterministic usage and delivery fixtures."""

import asyncio
import json
import sys
from collections import Counter
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

import pytest

from conftest import write_config
from drain_standin import DrainStandIn
from fakes import FixedClock
from plotroom.adapters.usage_fixture import FixtureUsageMeter
from plotroom.app.config import load_config
from plotroom.app.runtime import ProjectRuntime
from plotroom.app.serve import Daemon
from plotroom.app.tokens import IssuedToken, TokenRegistry
from plotroom.core.budgets import BUDGET_THRESHOLD
from plotroom.core.events import format_time
from plotroom.core.ledger import Ledger
from plotroom.ports.usage_meter import UsageObservation
from test_daemon import run_daemon, stop
from test_epic3_e2e import ToolCall, drain_tools, tools
from test_transport_contract import Endpoint

WORKERS = ("relay", "desktop")


@dataclass
class GuardrailsFixture:
    config: Path
    relay_state: Path
    clock: FixedClock
    meter: FixtureUsageMeter
    runtimes: list[ProjectRuntime]
    orchestrator: IssuedToken
    drain: IssuedToken
    worker: IssuedToken

    @asynccontextmanager
    async def serve(self) -> AsyncGenerator[tuple[Endpoint, Ledger]]:
        daemon = Daemon(
            load_config(self.config, {}), clock=self.clock, usage_meters={"demo": self.meter}
        )
        task, port = await run_daemon(daemon)
        try:
            yield (
                Endpoint(
                    f"http://127.0.0.1:{port}/mcp",
                    self.config,
                    self.orchestrator,
                    self.drain,
                    self.worker,
                    asyncio.Queue(),
                ),
                daemon.ledgers["demo"],
            )
        finally:
            await stop(daemon, task)

    async def observe(self, spend: float) -> None:
        runtime = self.runtimes[-1]
        self.clock.advance(runtime.interval.total_seconds())
        self.meter.readings = tuple(
            UsageObservation("fixture", worker, spend, self.clock.now()) for worker in WORKERS
        )
        assert await runtime.tick_if_due()


@pytest.fixture
def guardrails(
    tmp_path: Path, token_home: Path, monkeypatch: pytest.MonkeyPatch
) -> GuardrailsFixture:
    runtimes: list[ProjectRuntime] = []
    start_runtime = ProjectRuntime.start

    def capture(runtime: ProjectRuntime) -> None:
        runtimes.append(runtime)
        start_runtime(runtime)

    async def manual_ticks(_runtime: ProjectRuntime) -> None:
        # Drive the real tick explicitly and leave the action runner and adapters running.
        await asyncio.Future[None]()

    monkeypatch.setattr(ProjectRuntime, "start", capture)
    monkeypatch.setattr(ProjectRuntime, "_ticks", manual_ticks)
    relay = tmp_path / "relay"
    relay.mkdir()
    for marker in ("available", "acked", "send-first"):
        (relay / marker).touch()
    child = Path(__file__).with_name("switchboard_child.py")
    command = json.dumps([sys.executable, str(child), str(relay)])
    config = write_config(tmp_path)
    with config.open("a", encoding="utf-8") as output:
        output.write(
            'drain_session = "fixture-drain"\n'
            '[projects.demo.workers.relay]\nswitchboard_address = "fixture-peer"\n'
            '[projects.demo.workers.desktop]\nclaude_session = "fixture-session"\n'
            '[projects.demo.switchboard]\nboard = "fixture-board"\n'
            f"command = {command}\n"
        )
    registry = TokenRegistry(load_config(config, {}).data_dir)
    return GuardrailsFixture(
        config,
        relay,
        FixedClock(),
        FixtureUsageMeter(),
        runtimes,
        registry.issue("demo", "orchestrator", local=True),
        registry.issue("demo", "drain", local=True),
        registry.issue("demo", "worker:relay", local=True),
    )


async def start_lane(call: ToolCall, worker: str) -> None:
    await call(
        "lane_start",
        request_id=f"start-{worker}",
        lane=f"lane-{worker}",
        worker=worker,
        branch=f"work-{worker}",
        work_items=[],
    )


async def wait_for_stops(ledger: Ledger, keys: set[str]) -> None:
    async with asyncio.timeout(10):
        while True:
            committed = ledger.commit_signal
            done = {
                event.fields["action_key"]
                for event in await ledger.events_after(0)
                if event.kind == "core.action_done"
            }
            if keys <= done:
                return
            await committed.wait()


async def test_budget_warns_at_50_and_80_and_stops_at_100_once(
    guardrails: GuardrailsFixture,
) -> None:
    receiver = DrainStandIn()
    async with (
        asyncio.timeout(20),
        guardrails.serve() as (endpoint, ledger),
        tools(endpoint, "http") as call,
        tools(endpoint, "http", token=endpoint.drain) as drain,
    ):
        ids: dict[str, str] = {}
        for worker in WORKERS:
            await start_lane(call, worker)
            opened = await call(
                "budget_open",
                request_id=f"budget-{worker}",
                run=worker,
                lane=f"lane-{worker}",
                cap_usd=1,
                provider="fixture",
                key_scope="staging",
            )
            ids[worker] = opened["id"]
        for percent in (50, 80, 100):
            await guardrails.observe(percent / 100)
            items = (await call("next"))["items"]
            assert Counter(
                item["dedup_key"] for item in items if item["kind"] == "budget"
            ) == Counter(f"budget:{budget}:{percent}" for budget in ids.values())
            for item in items:
                if item["kind"] == "budget":
                    await call("ack", request_id=f"ack-{item['id']}", item=item["id"])
            if percent < 100:
                assert await ledger.pending_action() is None
                assert not any(
                    event.kind == "core.action_done" for event in await ledger.events_after(0)
                )
                assert (await drain("wait", timeout=0))["item"] is None
                assert not (guardrails.relay_state / "send-requests.jsonl").exists()
        stop_keys = {worker: f"budget:{budget}:100" for worker, budget in ids.items()}
        lease = await receiver.drain_one(drain_tools(drain))
        assert lease["action_key"] == stop_keys["desktop"]
        assert receiver.deliveries == {stop_keys["desktop"]: ("fixture-session", "stop")}
        await wait_for_stops(ledger, set(stop_keys.values()))

    async with asyncio.timeout(20), guardrails.serve() as (endpoint, ledger):
        async with (
            tools(endpoint, "http") as call,
            tools(endpoint, "http", token=endpoint.drain) as drain,
        ):
            await guardrails.observe(1.01)
            statuses = (await call("budget_status"))["budgets"]
            assert {row["run"]: (row["spent_usd"], row["as_of"]) for row in statuses} == {
                worker: (1.01, format_time(guardrails.clock.now())) for worker in WORKERS
            }
            assert all(item["kind"] != "budget" for item in (await call("next"))["items"])
            assert (
                await drain(
                    "outbox_lease", consumer_id=receiver.consumer_id, request_id="after-restart"
                )
            )["lease"] is None
            assert await ledger.pending_action() is None
        events = await ledger.events_after(0)
        assert Counter(
            event.upstream_id for event in events if event.kind == BUDGET_THRESHOLD
        ) == Counter(f"{budget}:{percent}" for budget in ids.values() for percent in (50, 80, 100))
        assert Counter(
            event.fields["action_key"] for event in events if event.kind == "core.action_done"
        ) == Counter(stop_keys.values())

    [request] = [
        json.loads(line)
        for line in (guardrails.relay_state / "send-requests.jsonl").read_text().splitlines()
    ]
    assert request["to"] == "fixture-peer"
    assert request["idempotency_key"] == stop_keys["relay"]
    assert json.loads(request["body"]) == {"action_key": stop_keys["relay"], "body": "stop"}


async def test_hands_on_request_routes_or_creates_one_next_item(
    guardrails: GuardrailsFixture,
) -> None:
    async with (
        asyncio.timeout(15),
        guardrails.serve() as (endpoint, _ledger),
        tools(endpoint, "http") as call,
    ):
        await start_lane(call, "relay")
        assignment = {"action_kind": "inspect", "lane": "lane-relay"}
        assert await call("route", request_id="assign", **assignment) == assignment
        assert (
            await call("route_request", request_id="resolve", action_kind="inspect") == assignment
        )
        assert all(item["kind"] != "route_missing" for item in (await call("next"))["items"])
        missing = await call("route_request", request_id="missing", action_kind="publish")
        assert missing["lane"] is None
        assert await call("route_request", request_id="again", action_kind="publish") == missing
        [item] = [row for row in (await call("next"))["items"] if row["kind"] == "route_missing"]
        assert item["id"] == missing["item"]
        assert item["dedup_key"] == "route_missing:publish"
        assert item["role"] == "orchestrator"
