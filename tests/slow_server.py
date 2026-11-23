"""A stateless MCP server with a slow tool, for proving what a cancel does on the server.

It stands in for ``wait()`` (entry 9) the way the real tool set will serve it: the same SDK, the same
stateless streamable HTTP. ``slow`` records that it started, then blocks until released; if the
request is canceled (the client's POST aborted) it records that instead. ``GET /state`` reports
what it saw and ``POST /release`` lets blocked calls finish, so a test waits on the state it needs and never sleeps.
"""

from __future__ import annotations

import asyncio
import contextlib
import sys
from collections.abc import AsyncGenerator
from typing import Any

import uvicorn
from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

POLL_TIMEOUT = 60.0


class SlowState:
    """What the slow tool has seen; every list names calls by the ``label`` they were given."""

    def __init__(self) -> None:
        self.started: list[str] = []
        self.finished: list[str] = []
        self.canceled: list[str] = []
        self.changed = asyncio.Event()  # set on every change; waiters clear it and re-check
        self.release = asyncio.Event()
        self.port = 0  # 0 picks a free port; a fixed one serves a person running it by hand

    def note(self, bucket: list[str], label: str) -> None:
        bucket.append(label)
        self.changed.set()

    async def until(self, bucket: str, label: str) -> None:
        """Return once ``label`` is in the named list; fails the test's own timeout if never."""
        async with asyncio.timeout(POLL_TIMEOUT):
            while label not in getattr(self, bucket):
                self.changed.clear()
                await self.changed.wait()

    def snapshot(self) -> dict[str, Any]:
        return {
            "started": list(self.started),
            "finished": list(self.finished),
            "canceled": list(self.canceled),
        }


def slow_app(state: SlowState) -> Starlette:
    server = MCPServer("slow")

    @server.tool()
    async def slow(label: str) -> str:  # pyright: ignore[reportUnusedFunction]
        """Block until released, recording how the call ended."""
        state.note(state.started, label)
        try:
            await state.release.wait()
        except asyncio.CancelledError:
            state.note(state.canceled, label)
            raise
        state.note(state.finished, label)
        return label

    @server.tool()
    def fast(label: str) -> str:  # pyright: ignore[reportUnusedFunction]
        """Answer at once."""
        return label

    app = server.streamable_http_app(
        stateless_http=True,
        transport_security=TransportSecuritySettings(allowed_hosts=["127.0.0.1:*", "localhost:*"]),
    )

    async def state_route(_: Request) -> JSONResponse:
        return JSONResponse(state.snapshot())

    async def release_route(_: Request) -> JSONResponse:
        state.release.set()
        return JSONResponse(state.snapshot())

    app.router.routes.append(Route("/state", state_route))
    app.router.routes.append(Route("/release", release_route, methods=["POST"]))
    return app


@contextlib.asynccontextmanager
async def serving_slow(state: SlowState) -> AsyncGenerator[str]:
    """Run the slow server on an ephemeral port; yields its MCP URL."""
    config = uvicorn.Config(
        slow_app(state), host="127.0.0.1", port=state.port, log_config=None, access_log=False
    )
    server = _Server(config)
    task = asyncio.ensure_future(server.serve())
    started = asyncio.ensure_future(server.bound.wait())
    await asyncio.wait({task, started}, return_when=asyncio.FIRST_COMPLETED)
    if task.done():
        task.result()
    try:
        yield f"http://127.0.0.1:{server.port}/mcp"
    finally:
        state.release.set()
        server.should_exit = True
        await asyncio.wait_for(task, timeout=POLL_TIMEOUT)


class _Server(uvicorn.Server):
    """uvicorn's server that says, through an event, when it listens and on which port."""

    def __init__(self, config: uvicorn.Config) -> None:
        super().__init__(config)
        self.bound = asyncio.Event()
        self.port = 0

    async def startup(self, sockets: Any = None) -> None:
        await super().startup(sockets=sockets)
        if self.started:
            self.port = int(self.servers[0].sockets[0].getsockname()[1])
            self.bound.set()


async def _main() -> int:
    state = SlowState()
    state.port = int(sys.argv[1]) if len(sys.argv) > 1 else 8766
    async with serving_slow(state) as url:
        print(f"slow server at {url}; state at {url.removesuffix('/mcp')}/state", flush=True)
        await asyncio.Event().wait()
    return 0


if __name__ == "__main__":  # a person's check: ``python tests/slow_server.py [port]``
    sys.exit(asyncio.run(_main()))
