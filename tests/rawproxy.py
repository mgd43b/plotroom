"""A ``plotroom connect --stdio`` subprocess driven with raw JSON lines, for tests."""

from __future__ import annotations

import asyncio
import contextlib
import json
import sys
from collections.abc import Callable
from typing import Any

from mcp_types import PROTOCOL_VERSION_META_KEY

from conftest import child_env
from slow_server import POLL_TIMEOUT

MODERN = "2026-07-28"
LEGACY = "2025-11-25"
ERAS = ["modern", "legacy"]
Call = Callable[[int, str, dict[str, Any]], dict[str, Any]]


class RawProxy:
    """A ``plotroom connect --stdio`` process driven with raw JSON lines."""

    def __init__(self, process: asyncio.subprocess.Process) -> None:
        self.process = process
        self.messages: list[dict[str, Any]] = []
        self.changed = asyncio.Event()
        self._reader = asyncio.ensure_future(self._read())

    async def _read(self) -> None:
        assert self.process.stdout is not None
        async for line in self.process.stdout:
            self.messages.append(json.loads(line))
            self.changed.set()

    def send(self, message: dict[str, Any]) -> None:
        assert self.process.stdin is not None
        self.process.stdin.write(json.dumps(message).encode() + b"\n")

    async def answer(self, request_id: int) -> dict[str, Any]:
        """The response to ``request_id``, once it has been relayed."""
        async with asyncio.timeout(POLL_TIMEOUT):
            while True:
                for message in self.messages:
                    if message.get("id") == request_id:
                        return message
                self.changed.clear()
                await self.changed.wait()

    async def close(self) -> None:
        assert self.process.stdin is not None
        self.process.stdin.close()
        try:
            with contextlib.suppress(ProcessLookupError):
                await asyncio.wait_for(self.process.wait(), timeout=POLL_TIMEOUT)
        except TimeoutError:
            self.process.kill()  # a proxy that will not exit must not outlive the test
            await self.process.wait()
            raise
        finally:
            self._reader.cancel()


async def spawn_proxy(*args: str, env: dict[str, str] | None = None) -> RawProxy:
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "plotroom",
        "connect",
        "--stdio",
        *args,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        env={**child_env(), **(env or {})},
    )
    return RawProxy(process)


def modern_call(request_id: int, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": "tools/call",
        "params": {
            "name": name,
            "arguments": arguments,
            "_meta": {
                PROTOCOL_VERSION_META_KEY: MODERN,
                "io.modelcontextprotocol/clientInfo": {"name": "test", "version": "0"},
                "io.modelcontextprotocol/clientCapabilities": {},
            },
        },
    }


def legacy_call(request_id: int, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    }


def initialize_request() -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": 0,
        "method": "initialize",
        "params": {
            "protocolVersion": LEGACY,
            "capabilities": {},
            "clientInfo": {"name": "test", "version": "0"},
        },
    }


def first_request(era: str) -> dict[str, Any]:
    """The first message a client of ``era`` sends: ``initialize``, or a call in the modern era."""
    return initialize_request() if era == "legacy" else modern_call(0, "snapshot", {})


async def connected(proxy: RawProxy, era: str) -> Call:
    """Do the era's handshake, if it has one, and return that era's ``tools/call`` builder."""
    if era == "modern":
        return modern_call
    proxy.send(initialize_request())
    reply = await proxy.answer(0)
    assert reply["result"]["protocolVersion"] == LEGACY
    proxy.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
    return legacy_call
