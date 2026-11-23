"""The MCP tool surface on stateless streamable HTTP (AD-7)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from mcp.server.mcpserver import Context, MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.datastructures import Headers
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp, Receive, Scope, Send

from plotroom.core.envelope import err, ok
from plotroom.core.events import LedgerError
from plotroom.core.ledger import Ledger
from plotroom.core.roles import Identity
from plotroom.core.snapshot import snapshot_result

type Authenticate = Callable[[str], Awaitable[Identity | None]]


@dataclass(frozen=True)
class ProjectRequest:
    identity: Identity
    ledger: Ledger | None


def allowed_host(host: str, patterns: Sequence[str]) -> bool:
    if host in patterns:
        return bool(host)
    for pattern in patterns:
        if pattern.endswith(":*") and host.startswith(pattern[:-1]):
            port = host[len(pattern) - 1 :]
            if port.isascii() and port.isdecimal() and len(port) <= 5 and 0 < int(port) <= 65535:
                return True
    return False


class _AccessMiddleware:
    """Check the request's address and target project before the MCP SDK sees its body."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        projects: frozenset[str],
        ledgers: Mapping[str, Ledger],
        allowed_hosts: Sequence[str],
        authenticate: Authenticate,
    ) -> None:
        self._app = app
        self._projects = projects
        self._ledgers = ledgers
        self._allowed_hosts = allowed_hosts
        self._authenticate = authenticate

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            headers = Headers(scope=scope)
            hosts = headers.getlist("host")
            if len(hosts) != 1 or not allowed_host(hosts[0], self._allowed_hosts):
                await Response(status_code=421)(scope, receive, send)
                return
            authorization = headers.getlist("authorization")
            identity = None
            if len(authorization) == 1:
                scheme, _, bearer = authorization[0].partition(" ")
                if scheme.lower() == "bearer" and bearer:
                    identity = await self._authenticate(bearer.lstrip(" "))
            if identity is None or identity.project_id not in self._projects:
                await Response(status_code=401)(scope, receive, send)
                return
            scope.setdefault("state", {})["plotroom"] = ProjectRequest(
                identity, self._ledgers.get(identity.project_id)
            )
        await self._app(scope, receive, send)


def _project_request(ctx: Context[Any, Request]) -> ProjectRequest:
    request = ctx.request_context.request
    if request is None:
        raise RuntimeError("MCP tool called without an HTTP request")
    caller: ProjectRequest = request.state.plotroom
    return caller


async def execute_command(
    ctx: Context[Any, Request], command: str, request_id: str, args: dict[str, Any]
) -> dict[str, Any]:
    """Run a tool's command as the authenticated role on the authenticated project's ledger."""
    caller = _project_request(ctx)
    if caller.ledger is None:
        return err("unavailable", "the project ledger is unavailable")
    try:
        result = await caller.ledger.execute(caller.identity.role, command, request_id, args)
    except LedgerError as exc:
        return err(exc.code, exc.message)
    return ok(result.result)


def build_app(
    project_ids: tuple[str, ...],
    allowed_hosts: Sequence[str],
    *,
    ledgers: Mapping[str, Ledger],
    authenticate: Authenticate,
    unavailable: str | None = None,
) -> Starlette:
    """The ASGI app serving the tool set at ``/mcp``.

    ``unavailable`` is set when the server is up but cannot honor calls (SQLite below the floor);
    every tool then answers ``unavailable``.
    """
    server = MCPServer("plotroom")

    @server.tool()
    def snapshot(ctx: Context[Any, Request]) -> dict[str, Any]:  # pyright: ignore[reportUnusedFunction]
        """Summarize the whole board: lanes, open items by kind, and the ranked item list."""
        caller = _project_request(ctx)
        if unavailable is None and caller.identity.role == "drain":
            return err("forbidden", "the drain role may only read the outbox")
        return snapshot_result(caller.identity.project_id, unavailable)

    app = server.streamable_http_app(
        stateless_http=True,
        transport_security=TransportSecuritySettings(allowed_hosts=list(allowed_hosts)),
    )
    app.add_middleware(
        _AccessMiddleware,
        projects=frozenset(project_ids),
        ledgers=ledgers,
        allowed_hosts=allowed_hosts,
        authenticate=authenticate,
    )
    return app
