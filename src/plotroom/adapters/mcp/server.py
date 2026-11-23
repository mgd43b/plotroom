"""The MCP tool surface on stateless streamable HTTP (AD-7)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
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
from plotroom.core.intake import record_claude
from plotroom.core.ledger import Ledger
from plotroom.core.roles import Identity
from plotroom.core.snapshot import (
    DEFAULT_SNAPSHOT_BUDGET,
    render_claims,
    render_next,
    render_snapshot,
)
from plotroom.ports.session_reader import SessionEvent

_NEXT_PAGE_SIZE = 64

type Authenticate = Callable[[str], Awaitable[Identity | None]]


@dataclass(frozen=True)
class ProjectRequest:
    identity: Identity
    ledger: Ledger | None


def _allowed_host(host: str, patterns: Sequence[str]) -> bool:
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
            if len(hosts) != 1 or not _allowed_host(hosts[0], self._allowed_hosts):
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
    snapshot_budget: int = DEFAULT_SNAPSHOT_BUDGET,
    next_budget: int = DEFAULT_SNAPSHOT_BUDGET,
) -> Starlette:
    """The ASGI app serving the tool set at ``/mcp``.

    ``unavailable`` is set when the server is up but cannot honor calls (SQLite below the floor);
    every tool then answers ``unavailable``.
    """
    server = MCPServer("plotroom")

    def board_access(ctx: Context[Any, Request]) -> ProjectRequest:
        caller = _project_request(ctx)
        if unavailable is not None:
            raise LedgerError("unavailable", unavailable)
        if caller.identity.role == "drain":
            raise LedgerError("forbidden", "the drain role may only read the outbox")
        return caller

    @server.tool()
    async def snapshot(  # pyright: ignore[reportUnusedFunction]
        ctx: Context[Any, Request],
        lane_offset: int = 0,
        lane: str | None = None,
    ) -> dict[str, Any]:
        """Summarize the board, page through current lanes, or read a named lane in full."""
        try:
            caller = board_access(ctx)
            if lane_offset < 0:
                return err("invalid", "lane_offset must be nonnegative")
            (cursor, items, lanes, details), github, claim_count = (
                ((0, [], {}, []), None, 0)
                if caller.ledger is None
                else await caller.ledger.read_snapshot(caller.identity.role)
            )
            if lane is not None:
                for detail in details:
                    if detail.id == lane:
                        return ok(detail.render())
                return err("not_found", "current lane not found")
            return ok(
                render_snapshot(
                    caller.identity.project_id,
                    cursor,
                    items,
                    lanes,
                    snapshot_budget,
                    lane_details=details,
                    lane_offset=lane_offset,
                    github=github,
                    github_error=None if caller.ledger is None else caller.ledger.github_error,
                    claim_count=claim_count,
                )
            )
        except LedgerError as exc:
            return err(exc.code, exc.message)

    async def next_items(
        ctx: Context[Any, Request],
        offset: int = 0,
        item: str | None = None,
    ) -> dict[str, Any]:
        """Page through visible next items in rank order, or read one open item in full."""
        try:
            caller = board_access(ctx)
            if not 0 <= offset <= 2**63 - 1:
                return err("invalid", "offset must be a nonnegative SQLite integer")
            if caller.ledger is None:
                raise LedgerError("unavailable", "the project ledger is unavailable")
            role = caller.identity.role
            if item is not None:
                entry = await caller.ledger.read_open_item(role, item)
                if entry is None:
                    return err("not_found", "open next item not found")
                return ok(asdict(entry))
            cursor, items = await caller.ledger.read_next(role, offset, _NEXT_PAGE_SIZE + 1)
            return ok(
                render_next(
                    cursor,
                    items[:_NEXT_PAGE_SIZE],
                    offset,
                    next_budget,
                    has_more=len(items) > _NEXT_PAGE_SIZE,
                )
            )
        except LedgerError as exc:
            return err(exc.code, exc.message)

    server.tool(name="next")(next_items)

    @server.tool()
    async def claims(  # pyright: ignore[reportUnusedFunction]
        ctx: Context[Any, Request],
        lane: str | None = None,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Page through visible lane claims and their reasons."""
        try:
            caller = board_access(ctx)
            if not 0 <= offset <= 2**63 - 1:
                return err("invalid", "offset must be a nonnegative SQLite integer")
            if caller.ledger is None:
                raise LedgerError("unavailable", "the project ledger is unavailable")
            cursor, entries = await caller.ledger.read_claims(
                caller.identity.role, lane, offset, _NEXT_PAGE_SIZE + 1
            )
            return ok(
                render_claims(
                    cursor,
                    entries[:_NEXT_PAGE_SIZE],
                    offset,
                    next_budget,
                    has_more=len(entries) > _NEXT_PAGE_SIZE,
                )
            )
        except LedgerError as exc:
            return err(exc.code, exc.message)

    @server.tool()
    async def overlaps(  # pyright: ignore[reportUnusedFunction]
        ctx: Context[Any, Request],
        pr: int | None = None,
        lane: str | None = None,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Page through visible overlap items for exactly one pull request or lane."""
        try:
            caller = board_access(ctx)
            if (lane is None) == (pr is None):
                return err("invalid", "provide exactly one of pr or lane")
            if pr is not None and not 0 < pr <= 2**63 - 1:
                return err("invalid", "pr must be a positive SQLite integer")
            if not 0 <= offset <= 2**63 - 1:
                return err("invalid", "offset must be a nonnegative SQLite integer")
            if caller.ledger is None:
                raise LedgerError("unavailable", "the project ledger is unavailable")
            cursor, items = await caller.ledger.read_overlaps(
                caller.identity.role, lane, pr, offset, _NEXT_PAGE_SIZE + 1
            )
            return ok(
                render_next(
                    cursor,
                    items[:_NEXT_PAGE_SIZE],
                    offset,
                    next_budget,
                    has_more=len(items) > _NEXT_PAGE_SIZE,
                )
            )
        except LedgerError as exc:
            return err(exc.code, exc.message)

    @server.tool()
    async def ingest(  # pyright: ignore[reportUnusedFunction]
        ctx: Context[Any, Request],
        kind: str,
        session_id: str,
        body: str,
        lane: str | None = None,
        upstream_id: str | None = None,
        turn_index: int | None = None,
    ) -> dict[str, Any]:
        """Report a Claude turn or session message from an authenticated project session."""
        caller = _project_request(ctx)
        if caller.ledger is None:
            return err("unavailable", unavailable or "the project ledger is unavailable")
        report = SessionEvent(kind, session_id, body, upstream_id, turn_index)
        session_worker = caller.ledger.session_workers.get(session_id)
        try:
            result = await caller.ledger.run(
                caller.identity.role,
                lambda uow: record_claude(uow, report, lane=lane, session_worker=session_worker),
            )
        except LedgerError as exc:
            return err(exc.code, exc.message)
        return ok({"event": result.event.id, "created": result.created})

    @server.tool()
    async def ack(  # pyright: ignore[reportUnusedFunction]
        ctx: Context[Any, Request],
        item: str,
        request_id: str,
        outcome: str | None = None,
    ) -> dict[str, Any]:
        """Acknowledge a next item."""
        args: dict[str, Any] = {"item": item}
        if outcome is not None:
            args["outcome"] = outcome
        if unavailable is not None:
            return err("unavailable", unavailable)
        return await execute_command(ctx, "ack", request_id, args)

    @server.tool()
    async def wait(  # pyright: ignore[reportUnusedFunction]
        ctx: Context[Any, Request],
        cursor: int | None = None,
        timeout: float | None = None,  # noqa: ASYNC109 - MCP tool argument
    ) -> dict[str, Any]:
        """Wait for the next visible board change, or return a cursor on timeout."""
        caller = _project_request(ctx)
        if caller.ledger is None:
            return err("unavailable", unavailable or "the project ledger is unavailable")

        async def progress(elapsed: float, total: float) -> None:
            await ctx.report_progress(elapsed, total, "Waiting for the next item")

        try:
            position, item = await caller.ledger.wait_for_item(
                caller.identity.role, cursor, timeout, progress
            )
        except LedgerError as exc:
            return err(exc.code, exc.message)
        return ok({"cursor": position, "item": item})

    @server.tool()
    async def lane_start(  # pyright: ignore[reportUnusedFunction]
        ctx: Context[Any, Request],
        lane: str,
        worker: str,
        branch: str,
        work_items: list[str],
        request_id: str,
    ) -> dict[str, Any]:
        """Start a lane for a configured worker."""
        return await execute_command(
            ctx,
            "lane_start",
            request_id,
            {"lane": lane, "worker": worker, "branch": branch, "work_items": work_items},
        )

    @server.tool()
    async def lane_update(  # pyright: ignore[reportUnusedFunction]
        ctx: Context[Any, Request],
        lane: str,
        waiting_on: str | None,
        request_id: str,
    ) -> dict[str, Any]:
        """Report who the lane is waiting on. Pass null when it can continue."""
        return await execute_command(
            ctx,
            "lane_update",
            request_id,
            {"lane": lane, "waiting_on": waiting_on or ""},
        )

    @server.tool()
    async def lane_ready(  # pyright: ignore[reportUnusedFunction]
        ctx: Context[Any, Request],
        lane: str,
        pr: int,
        head_sha: str,
        request_id: str,
    ) -> dict[str, Any]:
        """Report the pull request and head ready for review."""
        return await execute_command(
            ctx,
            "lane_ready",
            request_id,
            {"lane": lane, "pr": pr, "head_sha": head_sha},
        )

    @server.tool()
    async def lane_archive(  # pyright: ignore[reportUnusedFunction]
        ctx: Context[Any, Request],
        lane: str,
        request_id: str,
    ) -> dict[str, Any]:
        """Archive the lane and release its worker."""
        return await execute_command(ctx, "lane_archive", request_id, {"lane": lane})

    @server.tool()
    async def claim(  # pyright: ignore[reportUnusedFunction]
        ctx: Context[Any, Request], lane: str, globs: list[str], reason: str, request_id: str
    ) -> dict[str, Any]:
        """Claim one or more paths for a lane and record the reason."""
        return await execute_command(
            ctx, "claim", request_id, {"lane": lane, "globs": globs, "reason": reason}
        )

    @server.tool()
    async def release(  # pyright: ignore[reportUnusedFunction]
        ctx: Context[Any, Request], lane: str, globs: list[str], request_id: str
    ) -> dict[str, Any]:
        """Release the requested path claims for a lane."""
        return await execute_command(ctx, "release", request_id, {"lane": lane, "globs": globs})

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
