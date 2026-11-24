import asyncio
from pathlib import Path
from typing import Any, cast
from unittest.mock import Mock

import pytest
from mcp import ClientSession
from mcp.client.client import Client

from fakes import FixedClock
from plotroom.adapters.store_sqlite import SqliteStore
from plotroom.app import bridge as bridge_app
from plotroom.core.actions import SWITCHBOARD_SEND
from plotroom.core.budgets import record_budget_observations
from plotroom.core.catalog import build_catalog, register_bridge
from plotroom.core.events import LedgerError, NewEvent
from plotroom.core.ledger import Ledger
from plotroom.core.trigger_actions import DeliveryRoute
from plotroom.core.unit_of_work import UnitOfWork
from plotroom.ports.usage_meter import UsageObservation


@pytest.mark.parametrize("complete_retry", [True, False], ids=["complete", "retry-limit"])
async def test_bridge_leases_survive_expiry_and_reuse_the_action_key(
    tmp_path: Path,
    complete_retry: bool,
) -> None:
    store = SqliteStore("demo", tmp_path)
    await store.migrate()
    clock = FixedClock()
    catalog = build_catalog()
    register_bridge(catalog, "fixture", {}, {}, action_attempts=2)
    ledger = Ledger(store, catalog, clock, bridge_workers=())
    ledger.start()

    async def enqueue(uow: UnitOfWork) -> str:
        cause = (await uow.append(NewEvent("core", "core.tick", "fixture"))).event
        return await uow.enqueue_action(cause, SWITCHBOARD_SEND, {"to": "peer", "body": "fixture"})

    async def no_progress(_elapsed: float, _cap: float) -> None:
        pass

    try:
        key = await ledger.run("system", enqueue)
        with pytest.raises(LedgerError, match="scoped to another board"):
            await ledger.execute("bridge", "bridge_lease", "other-board", {"board": "other"})
        cursor, item = await ledger.wait_for_item("bridge", None, 0, no_progress)
        assert item is not None
        assert item["kind"] == "bridge_ready"
        first = (
            await ledger.execute("bridge", "bridge_lease", "first", {"board": "fixture"})
        ).result["lease"]
        assert first["action_key"] == key
        assert (
            await ledger.execute("bridge", "bridge_lease", "second", {"board": "fixture"})
        ).result["lease"] is None
        assert (await ledger.wait_for_item("bridge", cursor, 0, no_progress))[1] is None
        clock.advance(61)
        _, item = await ledger.wait_for_item("bridge", cursor, 0, no_progress)
        assert item is not None
        assert item["kind"] == "bridge_ready"
        retry = (
            await ledger.execute("bridge", "bridge_lease", "retry", {"board": "fixture"})
        ).result["lease"]
        assert retry["action_key"] == key
        assert retry["act_id"] != first["act_id"]
        with pytest.raises(LedgerError, match="current bridge lease"):
            await ledger.execute("bridge", "bridge_done", "stale", {"act_id": first["act_id"]})
        if complete_retry:
            assert (
                await ledger.execute(
                    "bridge", "bridge_done", "current", {"act_id": retry["act_id"]}
                )
            ).result == {"action_key": key, "state": "done"}
        else:
            clock.advance(61)
            exhausted = await ledger.execute(
                "bridge", "bridge_lease", "exhausted", {"board": "fixture"}
            )
            assert exhausted.result["lease"] is None
            items = (await ledger.read_board("orchestrator"))[1]
            [failed] = [item for item in items if item.kind == "failed_action"]
            assert failed.context["action_key"] == key
        assert await ledger.pending_action() is None
    finally:
        await ledger.stop()
        await store.close()


async def test_reassigned_worker_never_receives_a_lane_pinned_stop(tmp_path: Path) -> None:
    store = SqliteStore("demo", tmp_path)
    await store.migrate()
    clock = FixedClock()
    routes = {"worker": DeliveryRoute("switchboard", "same-address")}
    catalog = build_catalog(frozenset(routes), delivery_routes=routes)
    register_bridge(catalog, "fixture", {}, routes)
    ledger = Ledger(store, catalog, clock, bridge_workers=tuple(routes))
    ledger.start()
    try:
        for lane in ("original", "replacement"):
            await ledger.execute(
                "orchestrator",
                "lane_start",
                f"start-{lane}",
                {"lane": lane, "worker": "worker", "branch": lane, "work_items": []},
            )
            if lane == "original":
                await ledger.execute(
                    "orchestrator",
                    "budget_open",
                    "open",
                    {
                        "run": "run",
                        "lane": lane,
                        "cap_usd": 1,
                        "provider": "fixture",
                        "key_scope": "staging",
                    },
                )
                await ledger.run(
                    "system",
                    lambda uow: record_budget_observations(
                        uow, (UsageObservation("fixture", "run", 1, clock.now()),)
                    ),
                )
                assert await ledger.pending_action() is not None
                await ledger.execute("orchestrator", "lane_archive", "archive", {"lane": lane})

        lease = await ledger.execute("bridge", "bridge_lease", "lease", {"board": "fixture"})
        assert lease.result["lease"] is None
        assert await ledger.pending_action() is None
        items = (await ledger.read_board("orchestrator"))[1]
        [failed] = [item for item in items if item.kind == "failed_action"]
        assert "cannot fence" in failed.context["reason"]
    finally:
        await ledger.stop()
        await store.close()


async def test_bridge_does_not_send_an_expired_lease(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    async def fake_tool(_client: Client, name: str, **_arguments: Any) -> dict[str, Any]:
        calls.append(name)
        if calls == ["wait"]:
            return {"item": {"kind": "bridge_ready"}}
        if name == "bridge_lease":
            return {
                "lease": {
                    "expires_at": "2000-01-01T00:00:00Z",
                    "act_id": "stale",
                    "action_key": "fixture",
                    "to": "peer",
                    "body": "fixture",
                }
            }
        raise asyncio.CancelledError

    monkeypatch.setattr(bridge_app, "_tool", fake_tool)
    relay = cast(ClientSession, Mock(spec=ClientSession))
    client = cast(Client, Mock(spec=Client))
    with pytest.raises(asyncio.CancelledError):
        await bridge_app._send(relay, client, "consumer", "fixture")  # pyright: ignore[reportPrivateUsage]
    assert calls == ["wait", "bridge_lease", "wait"]
    assert cast(Mock, relay).mock_calls == []
