"""The stdio proxy as a subprocess against a real stateless MCP server with a slow tool.

Every wait is on state the server itself reports (``SlowState``), never on a sleep.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import subprocess
import sys
from collections.abc import AsyncGenerator
from textwrap import dedent

import pytest

from conftest import child_env
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


async def test_closing_stdout_ends_the_proxy_while_stdin_is_still_open(
    slow: tuple[SlowState, str],
) -> None:
    state, url = slow
    child = await asyncio.to_thread(
        subprocess.Popen,
        [sys.executable, "-m", "plotroom", "connect", "--stdio", "--url", url],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        env=child_env(),
    )
    assert child.stdin is not None
    assert child.stdout is not None
    stdin, stdout = child.stdin, child.stdout
    try:
        # Split a message across pipe reads and exceed the default asyncio line limit.
        prefix = b" " * (128 * 1024)
        first = modern_call(1, "fast", {"label": "before disconnect"})

        def send_first() -> None:
            stdin.write(prefix)
            stdin.write(json.dumps(first).encode() + b"\n")
            stdin.flush()

        async with asyncio.timeout(POLL_TIMEOUT):
            await asyncio.to_thread(send_first)
            answer = json.loads(await asyncio.to_thread(stdout.readline))
        assert answer["id"] == 1
        assert answer["result"]["isError"] is False
        assert answer["result"]["content"] == [{"type": "text", "text": "before disconnect"}]

        second = modern_call(2, "slow", {"label": "pending at disconnect"})
        stdin.write(json.dumps(second).encode() + b"\n")
        stdin.flush()
        await state.until("started", "pending at disconnect")
        stdout.close()
        # Keep our write end open and send nothing until after the process has exited.
        assert await asyncio.to_thread(child.wait, timeout=5) == 0
        assert not stdin.closed
        await state.until("canceled", "pending at disconnect")
    finally:
        if child.poll() is None:
            child.kill()
        await asyncio.to_thread(child.wait, timeout=5)
        stdin.close()
        stdout.close()


async def test_stdin_eof_drains_the_final_unterminated_message(
    slow: tuple[SlowState, str],
) -> None:
    _, url = slow
    proxy = await spawn_proxy("--url", url)
    try:
        stdin = proxy.process.stdin
        assert stdin is not None
        stdin.write(json.dumps(modern_call(1, "fast", {"label": "final line"})).encode())
        await stdin.drain()
        stdin.close()
        answer = await proxy.answer(1)
        assert answer["result"]["content"] == [{"type": "text", "text": "final line"}]
    finally:
        await proxy.close()
    assert proxy.process.returncode == 0


@pytest.mark.parametrize("disconnect", [True, False], ids=["disconnect", "resume"])
async def test_a_full_stdout_pipe_handles_disconnect_or_resumption(
    slow: tuple[SlowState, str], disconnect: bool
) -> None:
    _, url = slow
    script = dedent("""
        import os, sys
        from plotroom.adapters.mcp.proxy import StdioProxy
        from plotroom.app.cli import main

        write = StdioProxy._write_blocking
        raw_write = os.write

        def mark_full(fd, data):
            try:
                return raw_write(fd, data)
            except BlockingIOError:
                if fd == sys.stdout.fileno():
                    print("stdout full", file=sys.stderr, flush=True)
                raise

        def mark_write(proxy, payload):
            print("writing response", file=sys.stderr, flush=True)
            write(proxy, payload)

        os.write = mark_full
        StdioProxy._write_blocking = mark_write
        raise SystemExit(main(sys.argv[1:]))
    """)
    read_fd, write_fd = os.pipe()
    with os.fdopen(read_fd, "rb") as reader, os.fdopen(write_fd, "wb") as writer:
        os.set_blocking(write_fd, False)
        queued = 0
        for padding in (b"x" * 4096, b"x"):
            with contextlib.suppress(BlockingIOError):
                while True:
                    queued += os.write(write_fd, padding)
        os.set_blocking(write_fd, disconnect)
        child = await asyncio.to_thread(
            subprocess.Popen,
            [sys.executable, "-c", script, "connect", "--stdio", "--url", url],
            stdin=subprocess.PIPE,
            stdout=writer,
            stderr=subprocess.PIPE,
            env=child_env(),
        )
        writer.close()
        assert child.stdin is not None
        assert child.stderr is not None
        stdin, stderr = child.stdin, child.stderr
        try:
            stdin.write(json.dumps(modern_call(1, "fast", {"label": "blocked"})).encode() + b"\n")
            stdin.flush()
            async with asyncio.timeout(POLL_TIMEOUT):
                assert await asyncio.to_thread(stderr.readline) == b"writing response\n"
                if disconnect:
                    reader.close()  # the child entered its write path and stdin stays open
                else:
                    assert await asyncio.to_thread(stderr.readline) == b"stdout full\n"
                    assert await asyncio.to_thread(reader.read, queued) == b"x" * queued
                    answer = json.loads(await asyncio.to_thread(reader.readline))
                    assert answer["result"]["content"] == [{"type": "text", "text": "blocked"}]
                    stdin.close()
            assert await asyncio.to_thread(child.wait, timeout=5) == 0
            assert stdin.closed == (not disconnect)
            assert b"BrokenPipeError" not in stderr.read()
        finally:
            if child.poll() is None:
                child.kill()
            await asyncio.to_thread(child.wait, timeout=5)
            stdin.close()
            stderr.close()
