"""OpenRouter metering against a configured daemon and isolated HTTP."""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import datetime
from functools import partial
from pathlib import Path
from typing import Any

import httpx
import pytest

from conftest import authenticated_client, tool_json
from fakes import FixedClock
from plotroom.adapters.openrouter import OpenRouterRun, OpenRouterUsageMeter
from plotroom.app import serve
from plotroom.app.config import ConfigError, load_config
from plotroom.app.runtime import ProjectRuntime
from plotroom.app.serve import Daemon
from plotroom.core.events import LedgerError
from test_daemon import bearer, run_daemon, stop

FIXTURE_KEY = "sk-or-v1-fixture-usage-key"
PIN = hashlib.sha256(FIXTURE_KEY.encode()).hexdigest()
RECEIPT = {
    "limit": 5,
    "limit_remaining": 4.9999967,
    "limit_reset": None,
    "is_management_key": False,
    "is_provisioning_key": False,
    "byok_usage": 0,
    "usage": 0.0000033,
}


def config_text() -> str:
    return f'''data_dir = "data"
port = 0
tick_seconds = 1
[projects.demo.workers.fixture]
[projects.demo.openrouter.trial]
key_file = "trial.key"
key_sha256 = "{PIN}"
'''


@pytest.mark.parametrize(
    "duplicate", ["projects.demo.openrouter.other", "projects.other.openrouter.trial"]
)
def test_a_key_binding_cannot_be_shared(tmp_path: Path, duplicate: str) -> None:
    path = tmp_path / "plotroom.toml"
    path.write_text(
        config_text() + f'\n[{duplicate}]\nkey_file = "copy.key"\nkey_sha256 = "{PIN.upper()}"\n'
    )
    with pytest.raises(ConfigError, match="unique key pin"):
        load_config(path, {})


@pytest.mark.parametrize(
    ("data", "status"),
    [
        ({}, 200),
        ({**RECEIPT, "limit": None}, 200),
        ({**RECEIPT, "limit": 6}, 200),
        ({**RECEIPT, "limit_reset": "monthly"}, 200),
        ({**RECEIPT, "is_management_key": True}, 200),
        ({**RECEIPT, "is_provisioning_key": True}, 200),
        ({**RECEIPT, "byok_usage": 0.001}, 200),
        ({**RECEIPT, "usage": "1.25"}, 200),
        ({**RECEIPT, "usage": -1}, 200),
        ({**RECEIPT, "usage": True}, 200),
        ({**RECEIPT, "limit_remaining": 6}, 200),
        (RECEIPT, 503),
        (RECEIPT, 302),
    ],
)
async def test_openrouter_rejects_unverified_data(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, data: dict[str, Any], status: int
) -> None:
    secret = tmp_path / "fixture.key"
    secret.write_text(FIXTURE_KEY)

    binding = OpenRouterRun(secret, PIN)

    async def registered() -> list[tuple[str, float, str]]:
        return [("trial", 5, binding.reservation)]

    calls: list[str] = []

    def reply(request: httpx.Request) -> httpx.Response:
        calls.append(request.method)
        return httpx.Response(
            status,
            json={"data": data, "echo": FIXTURE_KEY},
            headers={"Location": "https://untrusted.invalid/"} if status == 302 else {},
        )

    meter = OpenRouterUsageMeter(
        {"trial": binding},
        registered,
        FixedClock(),
        transport=httpx.MockTransport(reply),
    )
    assert await meter.observations() == ()
    assert calls == ["GET"]
    assert "OpenRouter usage observation unavailable" in caplog.text
    assert FIXTURE_KEY not in caplog.text


async def test_daemon_polls_a_pinned_openrouter_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    path = tmp_path / "plotroom.toml"
    path.write_text(config_text())
    key_file = tmp_path / "trial.key"
    key_file.write_text(FIXTURE_KEY + "\n")
    config = load_config(path, {})
    config.data_dir.mkdir()
    (config.data_dir / "inactive.db").touch()
    clock = FixedClock()
    runtimes: list[ProjectRuntime] = []
    calls = 0
    spend = 0.0000033

    def reply(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        assert str(request.url) == "https://openrouter.ai/api/v1/key"
        assert request.method == "GET"
        assert request.headers["authorization"] == f"Bearer {FIXTURE_KEY}"
        return httpx.Response(200, json={"data": {**RECEIPT, "usage": spend, "label": FIXTURE_KEY}})

    monkeypatch.setattr(
        serve,
        "OpenRouterUsageMeter",
        partial(OpenRouterUsageMeter, transport=httpx.MockTransport(reply)),
    )

    def capture(runtime: ProjectRuntime) -> None:
        runtimes.append(runtime)

    monkeypatch.setattr(ProjectRuntime, "start", capture)

    async def tick() -> None:
        clock.advance(1)
        assert await runtimes[-1].tick_if_due()

    daemon = Daemon(config, clock=clock)
    task, port = await run_daemon(daemon)
    try:
        await tick()
        assert calls == 0
        async with authenticated_client(f"http://127.0.0.1:{port}/mcp", bearer(config)) as client:
            started = tool_json(
                await client.call_tool(
                    "lane_start",
                    {
                        "lane": "lane",
                        "worker": "fixture",
                        "branch": "work",
                        "work_items": [],
                        "request_id": "start",
                    },
                )
            )
            assert started["ok"]
            args = {
                "run": "trial",
                "lane": "lane",
                "cap_usd": 5,
                "provider": "openrouter",
                "key_scope": "staging",
            }
            missing = tool_json(
                await client.call_tool(
                    "budget_open", {**args, "run": "unconfigured", "request_id": "missing"}
                )
            )
            assert not missing["ok"]
            assert missing["error"]["code"] == "invalid"
            assert tool_json(await client.call_tool("budget_open", {**args, "request_id": "open"}))[
                "ok"
            ]
            await tick()
            outcome = tool_json(await client.call_tool("budget_status", {"run": "trial"}))
            [record] = outcome["data"]["budgets"]
            assert record["spent_usd"] == 0.0000033
            assert datetime.fromisoformat(record["as_of"]) == clock.now()
            assert FIXTURE_KEY not in str(outcome)
            assert PIN not in str(outcome)
    finally:
        await stop(daemon, task)

    # Replacing both the file and pin still cannot move an existing budget.
    replacement = "sk-or-v1-replacement-fixture"
    key_file.write_text(replacement)
    path.write_text(config_text().replace(PIN, hashlib.sha256(replacement.encode()).hexdigest()))
    rebound = Daemon(load_config(path, {}), clock=clock)
    task, _ = await run_daemon(rebound)
    try:
        before = calls
        await tick()
        assert calls == before
        assert await rebound.ledgers["demo"].budget_status("orchestrator", "trial") == [record]
    finally:
        await stop(rebound, task)

    # Removing the old configuration does not release its key for another run.
    key_file.write_text(FIXTURE_KEY)
    path.write_text(config_text().replace("openrouter.trial]", "openrouter.other]"))
    reused = Daemon(load_config(path, {}), clock=clock)
    task, _ = await run_daemon(reused)
    try:
        with pytest.raises(LedgerError, match="already reserved") as error:
            await reused.ledgers["demo"].execute(
                "orchestrator", "budget_open", "reuse", {**args, "run": "other"}
            )
        assert error.value.code == "conflict"
    finally:
        await stop(reused, task)

    # Reservations in an inactive project still rule out reuse in another project.
    path.write_text(config_text().replace("projects.demo.", "projects.other."))
    moved = Daemon(load_config(path, {}), clock=clock)
    task, _ = await run_daemon(moved)
    try:
        ledger = moved.ledgers["other"]
        await ledger.execute(
            "orchestrator",
            "lane_start",
            "start",
            {
                "lane": "lane",
                "worker": "fixture",
                "branch": "work",
                "work_items": [],
            },
        )
        with pytest.raises(LedgerError, match="configured OpenRouter key binding") as error:
            await ledger.execute("orchestrator", "budget_open", "move-key", args)
        assert error.value.code == "invalid"
    finally:
        await stop(moved, task)

    restarted = Daemon(config, clock=clock)
    task, _ = await run_daemon(restarted)
    try:
        ledger = restarted.ledgers["demo"]
        for stale_spend in (0.000001, 0.0000033):
            spend = stale_spend
            await tick()
            assert await ledger.budget_status("orchestrator", "trial") == [record]
        before = calls
        key_file.write_text("sk-or-v1-other-fixture")
        spend = 0.01
        await tick()
        assert calls == before
        assert await ledger.budget_status("orchestrator", "trial") == [record]
        key_file.write_text(FIXTURE_KEY)
        await tick()
        [fresh] = await ledger.budget_status("orchestrator", "trial")
        assert fresh["spent_usd"] == 0.01
        assert datetime.fromisoformat(fresh["as_of"]) == clock.now()
    finally:
        await stop(restarted, task)
    assert FIXTURE_KEY not in caplog.text
    assert PIN not in caplog.text
    for database in config.data_dir.glob("*.db*"):
        contents = database.read_bytes()
        assert FIXTURE_KEY.encode() not in contents
        assert PIN.encode() not in contents


@pytest.mark.parametrize(
    "body",
    [
        b'{"data":{"usage":1e9999999999999999999}}',
        b'{"data":' + b"[" * 2000 + b"0" + b"]" * 2000 + b"}",
        b'{"data":{"usage":0,' + json.dumps(RECEIPT).encode()[1:] + b"}",
    ],
)
async def test_openrouter_malformed_json_does_not_escape_poller(
    tmp_path: Path, body: bytes
) -> None:
    secret = tmp_path / "fixture.key"
    secret.write_text(FIXTURE_KEY)
    binding = OpenRouterRun(secret, PIN)

    async def registered() -> list[tuple[str, float, str]]:
        return [("trial", 5, binding.reservation)]

    meter = OpenRouterUsageMeter(
        {"trial": binding},
        registered,
        FixedClock(),
        transport=httpx.MockTransport(lambda _: httpx.Response(200, content=body)),
    )
    assert await meter.observations() == ()


async def test_a_slow_openrouter_key_does_not_discard_other_runs(tmp_path: Path) -> None:
    runs: dict[str, OpenRouterRun] = {}
    for run in ("slow", "healthy"):
        secret = tmp_path / run
        key = f"sk-or-v1-{run}-fixture"
        secret.write_text(key)
        runs[run] = OpenRouterRun(secret, hashlib.sha256(key.encode()).hexdigest())

    async def registered() -> list[tuple[str, float, str]]:
        return [(run, 5, binding.reservation) for run, binding in runs.items()]

    async def reply(request: httpx.Request) -> httpx.Response:
        if "slow" in request.headers["authorization"]:
            await asyncio.Event().wait()
        return httpx.Response(200, json={"data": RECEIPT})

    meter = OpenRouterUsageMeter(
        runs,
        registered,
        FixedClock(),
        timeout=0.2,
        transport=httpx.MockTransport(reply),
    )
    async with asyncio.timeout(2):
        [reading] = await meter.observations()
    assert reading.run == "healthy"
    assert reading.spent_usd == 0.0000033
