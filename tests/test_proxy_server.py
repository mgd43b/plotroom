"""The stdio proxy as a subprocess against a real stateless MCP server with a slow tool.

Every wait is on state the server itself reports (``SlowState``), never on a sleep.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator

import pytest

from rawproxy import ERAS, connected, modern_call, spawn_proxy
from slow_server import POLL_TIMEOUT, SlowState, serving_slow


@pytest.fixture
async def slow() -> AsyncGenerator[tuple[SlowState, str]]:
    state = SlowState()
    async with serving_slow(state) as url:
        yield state, url


@pytest.mark.parametrize("era", ERAS)
async def test_a_fast_call_is_answered_while_a_slow_one_is_pending(
    slow: tuple[SlowState, str], era: str
) -> None:
    state, url = slow
    proxy = await spawn_proxy("--url", url)
    try:
        call = await connected(proxy, era)
        proxy.send(call(1, "slow", {"label": "long"}))
        await state.until("started", "long")
        proxy.send(call(2, "fast", {"label": "quick"}))
        quick = await proxy.answer(2)
        assert quick["result"]["isError"] is False
        assert [m["id"] for m in proxy.messages if "result" in m and m["id"] > 0] == [2]
        state.release.set()
        assert (await proxy.answer(1))["result"]["isError"] is False
    finally:
        await proxy.close()
    assert state.finished == ["long"]


@pytest.mark.parametrize("era", ERAS)
async def test_canceling_a_slow_call_ends_it_on_the_server_and_relays_nothing_for_it(
    slow: tuple[SlowState, str], era: str
) -> None:
    state, url = slow
    proxy = await spawn_proxy("--url", url)
    try:
        call = await connected(proxy, era)
        proxy.send(call(1, "slow", {"label": "doomed"}))
        proxy.send(call(2, "slow", {"label": "survivor"}))
        await state.until("started", "doomed")
        await state.until("started", "survivor")
        proxy.send(
            {"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": 1}}
        )
        await state.until("canceled", "doomed")
        assert state.canceled == ["doomed"], "only the canceled call ends"
        proxy.send(call(3, "fast", {"label": "after"}))
        await proxy.answer(3)
        assert not [m for m in proxy.messages if m.get("id") == 1]
        state.release.set()
        await proxy.answer(2)
    finally:
        await proxy.close()
    assert state.finished == ["survivor"]


async def test_closing_stdin_ends_the_proxy_even_with_a_listen_stream_open(
    slow: tuple[SlowState, str],
) -> None:
    """Claude Code opens ``subscriptions/listen`` and never closes it; the proxy must still exit."""
    _, url = slow
    proxy = await spawn_proxy("--url", url)
    listen = modern_call(1, "tools/call", {})
    listen["method"] = "subscriptions/listen"
    listen["params"] = {
        "notifications": {"toolsListChanged": True},
        "_meta": listen["params"]["_meta"],
    }
    try:
        proxy.send(listen)
        async with asyncio.timeout(POLL_TIMEOUT):
            while not any("subscriptionId" in str(m) for m in proxy.messages):
                proxy.changed.clear()
                await proxy.changed.wait()
    finally:
        # waits for the process to exit, which a held-open stream would prevent, and kills it if not
        await proxy.close()
    assert proxy.process.returncode == 0
