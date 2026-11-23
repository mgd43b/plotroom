"""Hands-on route assignment, startup seeding, and durable missing-route items."""

from dataclasses import replace
from pathlib import Path

import pytest

from plotroom.app.config import load_config
from plotroom.app.serve import Daemon
from plotroom.core.events import LedgerError
from test_daemon import run_daemon, stop

ORCHESTRATOR = "orchestrator"
WORKER = "worker:fixture"


async def test_routes_seed_a_whole_project_once(tmp_path: Path) -> None:
    config_path = tmp_path / "plotroom.toml"
    config_path.write_text(
        'data_dir = "state"\nport = 0\n'
        '[projects.demo.routes]\npublish = "future-publisher"\naudit = "future-auditor"\n'
        '[projects.other.routes]\npublish = "other-publisher"\n',
        encoding="utf-8",
    )
    daemon = Daemon(load_config(config_path, {}))
    task, _ = await run_daemon(daemon)
    try:
        demo = daemon.ledgers["demo"]
        for kind, expected in (("publish", "future-publisher"), ("audit", "future-auditor")):
            result = await demo.execute(ORCHESTRATOR, "route_request", kind, {"action_kind": kind})
            assert result.result == {"action_kind": kind, "lane": expected}
        other = await daemon.ledgers["other"].execute(
            ORCHESTRATOR, "route_request", "publish", {"action_kind": "publish"}
        )
        assert other.result == {"action_kind": "publish", "lane": "other-publisher"}
        await demo.execute(
            ORCHESTRATOR, "route", "assign", {"action_kind": "publish", "lane": "publisher"}
        )
    finally:
        await stop(daemon, task)

    config_path.write_text(
        'data_dir = "state"\nport = 0\n'
        '[projects.demo.routes]\npublish = "config-override"\nnew = "new-config-route"\n',
        encoding="utf-8",
    )
    daemon = Daemon(load_config(config_path, {}))
    task, _ = await run_daemon(daemon)
    try:
        demo = daemon.ledgers["demo"]
        for kind, expected in (("publish", "publisher"), ("audit", "future-auditor")):
            result = await demo.execute(
                ORCHESTRATOR, "route_request", f"after-{kind}", {"action_kind": kind}
            )
            assert result.result == {"action_kind": kind, "lane": expected}
        missing = await demo.execute(
            ORCHESTRATOR, "route_request", "new-kind", {"action_kind": "new"}
        )
        assert missing.result["lane"] is None
        [item] = (await demo.read_next(ORCHESTRATOR, 0, 100))[1]
        assert item.id == missing.result["item"]
        assert item.dedup_key == "route_missing:new"
    finally:
        await stop(daemon, task)


async def test_missing_route_stays_durable_and_snoozed_until_assigned(tmp_path: Path) -> None:
    config_path = tmp_path / "plotroom.toml"
    config_path.write_text('data_dir = "state"\nport = 0\n[projects.demo]\n', encoding="utf-8")
    config = load_config(config_path, {})
    args = {"action_kind": "release"}
    assignment = {**args, "lane": "release-owner"}
    daemon = Daemon(config)
    task, _ = await run_daemon(daemon)
    try:
        ledger = daemon.ledgers["demo"]
        for command, denied_args in (("route", assignment), ("route_request", args)):
            with pytest.raises(LedgerError) as denied:
                await ledger.execute(WORKER, command, "unauthorized", denied_args)
            assert denied.value.code == "forbidden"
        assert (await ledger.read_next(ORCHESTRATOR, 0, 100))[1] == []
        for invalid in (" ", "abc\x00def", "\ud800", "x" * 129):
            with pytest.raises(LedgerError) as bad_label:
                await ledger.execute(
                    ORCHESTRATOR, "route_request", "invalid", {"action_kind": invalid}
                )
            assert bad_label.value.code == "invalid"
        first = await ledger.execute(ORCHESTRATOR, "route_request", "first", args)
        duplicate = await ledger.execute(ORCHESTRATOR, "route_request", "first", args)
        second = await ledger.execute(ORCHESTRATOR, "route_request", "second", args)
        [item] = (await ledger.read_next(ORCHESTRATOR, 0, 100))[1]
        expected = {**args, "lane": None, "item": item.id}
        assert first.result == duplicate.result == second.result == expected
        assert not first.replayed
        assert duplicate.replayed
        assert not second.replayed
        assert item.role == ORCHESTRATOR
        assert item.dedup_key == "route_missing:release"
        assert item.context == args
        acked = await ledger.execute(ORCHESTRATOR, "ack", "ack", {"item": item.id})
        assert acked.result["state"] == "snoozed"
        assert (await ledger.read_next(ORCHESTRATOR, 0, 100))[1] == []
    finally:
        await stop(daemon, task)

    daemon = Daemon(config)
    task, _ = await run_daemon(daemon)
    try:
        ledger = daemon.ledgers["demo"]
        third = await ledger.execute(ORCHESTRATOR, "route_request", "third", args)
        assert third.result == first.result
        assert (await ledger.read_next(ORCHESTRATOR, 0, 100))[1] == []
        saved = await ledger.execute(ORCHESTRATOR, "route", "assign", assignment)
        assert saved.result == assignment
        active = await ledger.run("system", lambda uow: uow.active_item("route_missing", "release"))
        assert active is None
        routed = await ledger.execute(ORCHESTRATOR, "route_request", "routed", args)
        assert routed.result == assignment
        replay = await ledger.execute(ORCHESTRATOR, "route_request", "first", args)
        assert replay.replayed
        assert replay.result == first.result
    finally:
        await stop(daemon, task)


async def test_first_seed_clears_a_missing_item_from_an_empty_ledger(tmp_path: Path) -> None:
    config_path = tmp_path / "plotroom.toml"
    config_path.write_text('data_dir = "state"\nport = 0\n[projects.demo]\n', encoding="utf-8")
    config = load_config(config_path, {})
    args = {"action_kind": "publish"}
    daemon = Daemon(config)
    task, _ = await run_daemon(daemon)
    try:
        ledger = daemon.ledgers["demo"]
        await ledger.execute(ORCHESTRATOR, "route_request", "before-config", args)
        assert len((await ledger.read_next(ORCHESTRATOR, 0, 100))[1]) == 1
    finally:
        await stop(daemon, task)

    daemon = Daemon(replace(config, routes={"demo": {"publish": "publisher"}}))
    task, _ = await run_daemon(daemon)
    try:
        ledger = daemon.ledgers["demo"]
        assert (await ledger.read_next(ORCHESTRATOR, 0, 100))[1] == []
        routed = await ledger.execute(ORCHESTRATOR, "route_request", "after-config", args)
        assert routed.result == {**args, "lane": "publisher"}
    finally:
        await stop(daemon, task)
