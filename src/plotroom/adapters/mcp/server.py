"""The MCP tool surface on stateless streamable HTTP (AD-7)."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import Annotated, Any, Literal

from mcp.server.mcpserver import Context, MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictFloat, StrictInt
from starlette.applications import Starlette
from starlette.datastructures import Headers
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp, Receive, Scope, Send

from plotroom.core.clerk_import import PullImportRow, SkipImportRow, TriggerImportRow
from plotroom.core.envelope import err, ok
from plotroom.core.events import LedgerError
from plotroom.core.intake import record_claude
from plotroom.core.ledger import Ledger
from plotroom.core.outbox import lease_request_id
from plotroom.core.roles import Identity
from plotroom.core.snapshot import (
    DEFAULT_SNAPSHOT_BUDGET,
    render_claims,
    render_next,
    render_search,
    render_snapshot,
)
from plotroom.ports.search import DEFAULT_SEARCH_LIMIT
from plotroom.ports.session_reader import SessionEvent
from plotroom.ports.work_items import WorkItemSource

_NEXT_PAGE_SIZE = 64

type Authenticate = Callable[[str], Awaitable[Identity | None]]


class ReadyChecklistEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    step: str
    owner: str
    ref: str


class GitHubDecisionCitation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    kind: Literal["github_comment"]
    repository: str
    issue: int
    comment_id: int


class ChatDecisionCitation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    kind: Literal["chat"]
    ref: str


DecisionCitation = Annotated[
    GitHubDecisionCitation | ChatDecisionCitation, Field(discriminator="kind")
]


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


def _private_local_request(ctx: Context[Any, Request]) -> bool:
    request = ctx.request_context.request
    if request is None:
        return False
    # Uvicorn supplies the Unix socket path and no port for the private listener.
    address = request.scope.get("server")
    return address is not None and address[1] is None and address[0].startswith("/")


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
    work_item_sources: Mapping[str, WorkItemSource] | None = None,
    unavailable: str | None = None,
    snapshot_budget: int = DEFAULT_SNAPSHOT_BUDGET,
    next_budget: int = DEFAULT_SNAPSHOT_BUDGET,
    search_budget: int = DEFAULT_SNAPSHOT_BUDGET,
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
            raise LedgerError("forbidden", "the drain role may only use wait and outbox tools")
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
            (cursor, items, lanes, details), github, trigger_counts, claim_count, freeze_reason = (
                ((0, [], {}, []), None, {}, 0, None)
                if caller.ledger is None
                else await caller.ledger.snapshot_summary(caller.identity.role, checklist_lane=lane)
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
                    trigger_counts=trigger_counts,
                    freeze_reason=freeze_reason,
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
    async def search(  # pyright: ignore[reportUnusedFunction]
        ctx: Context[Any, Request],
        text: str,
        kinds: list[str] | None = None,
        limit: int = DEFAULT_SEARCH_LIMIT,
    ) -> dict[str, Any]:
        """Find a word or phrase in the current project's visible committed records."""
        try:
            caller = board_access(ctx)
            if caller.ledger is None:
                raise LedgerError("unavailable", "the project ledger is unavailable")
            page = await caller.ledger.search(
                text, None if kinds is None else tuple(kinds), limit, role=caller.identity.role
            )
            return ok(render_search(page, search_budget))
        except LedgerError as exc:
            return err(exc.code, exc.message)

    @server.tool()
    async def brief(  # pyright: ignore[reportUnusedFunction]
        ctx: Context[Any, Request], work_item: str, worker: str
    ) -> dict[str, Any]:
        """Read a proposed lane brief filled from the ticket and current path claims."""
        try:
            caller = board_access(ctx)
            if caller.ledger is None:
                raise LedgerError("unavailable", "the project ledger is unavailable")
            source = (
                None
                if work_item_sources is None
                else work_item_sources.get(caller.identity.project_id)
            )
            text = await caller.ledger.brief(caller.identity.role, work_item, worker, source)
            return ok({"brief": text})
        except LedgerError as exc:
            return err(exc.code, exc.message)

    @server.tool()
    async def merge_gate(  # pyright: ignore[reportUnusedFunction]
        ctx: Context[Any, Request],
        pr: int,
        expected_head_sha: str | None = None,
        expected_main_sha: str | None = None,
    ) -> dict[str, Any]:
        """Read the current merge readiness, checked head, and observation watermark for a PR."""
        try:
            caller = board_access(ctx)
            if caller.ledger is None:
                raise LedgerError("unavailable", "the project ledger is unavailable")
            verdict = await caller.ledger.merge_gate(
                caller.identity.role,
                pr,
                expected_head_sha=expected_head_sha,
                expected_main_sha=expected_main_sha,
            )
            return ok(asdict(verdict))
        except LedgerError as exc:
            return err(exc.code, exc.message)

    @server.tool()
    async def claims(  # pyright: ignore[reportUnusedFunction]
        ctx: Context[Any, Request],
        lane: str | None = None,
        offset: int = 0,
        detail: bool = False,
    ) -> dict[str, Any]:
        """Page through visible claims. Use detail with a row's offset for its full reason."""
        try:
            caller = board_access(ctx)
            if not 0 <= offset <= 2**63 - 1:
                return err("invalid", "offset must be a nonnegative SQLite integer")
            if caller.ledger is None:
                raise LedgerError("unavailable", "the project ledger is unavailable")
            cursor, entries = await caller.ledger.read_claims(
                caller.identity.role, lane, offset, 1 if detail else _NEXT_PAGE_SIZE + 1
            )
            if detail:
                return ok({"cursor": cursor, "claims": [entry.render() for entry in entries]})
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
        """Page through visible overlaps for one PR or lane. Use next(item=...) for full detail."""
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
        branch_pattern: str | None = None,
    ) -> dict[str, Any]:
        """Start a lane with an optional case-sensitive branch glob for merge checks."""
        args = {"lane": lane, "worker": worker, "branch": branch, "work_items": work_items}
        if branch_pattern is not None:
            args["branch_pattern"] = branch_pattern
        return await execute_command(
            ctx,
            "lane_start",
            request_id,
            args,
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
        checklist: list[ReadyChecklistEntry] | None = None,
    ) -> dict[str, Any]:
        """Report the pull request and head ready for review with optional post-merge steps."""
        args: dict[str, Any] = {"lane": lane, "pr": pr, "head_sha": head_sha}
        if checklist is not None:
            args["checklist"] = [entry.model_dump() for entry in checklist]
        return await execute_command(ctx, "lane_ready", request_id, args)

    @server.tool()
    async def lane_archive(  # pyright: ignore[reportUnusedFunction]
        ctx: Context[Any, Request],
        lane: str,
        request_id: str,
    ) -> dict[str, Any]:
        """Archive the lane and release its worker."""
        return await execute_command(ctx, "lane_archive", request_id, {"lane": lane})

    @server.tool()
    async def deploy_report(  # pyright: ignore[reportUnusedFunction]
        ctx: Context[Any, Request],
        lane: str,
        environment: str,
        sha: str,
        state: str,
        request_id: str,
    ) -> dict[str, Any]:
        """Report a deployment status for the calling worker's lane."""
        return await execute_command(
            ctx,
            "deploy_report",
            request_id,
            {"lane": lane, "environment": environment, "sha": sha, "state": state},
        )

    @server.tool()
    async def clerk_import(  # pyright: ignore[reportUnusedFunction]
        ctx: Context[Any, Request],
        triggers: list[TriggerImportRow],
        pulls: list[PullImportRow],
        skips: list[SkipImportRow],
        ledger_repository: str | None = None,
    ) -> dict[str, Any]:
        """Import typed clerk template rows over the private local listener."""
        if not _private_local_request(ctx):
            return err("forbidden", "clerk import requires the private local listener")
        args: dict[str, Any] = {"triggers": triggers, "pulls": pulls, "skips": skips}
        if ledger_repository is not None:
            args["ledger_repository"] = ledger_repository
        canonical = json.dumps(args, sort_keys=True, separators=(",", ":"), allow_nan=False)
        request_id = "clerk-import:" + hashlib.sha256(canonical.encode()).hexdigest()
        return await execute_command(ctx, "clerk_import", request_id, args)

    @server.tool()
    async def checklist_import(  # pyright: ignore[reportUnusedFunction]
        ctx: Context[Any, Request],
        lane: str,
        rows: list[ReadyChecklistEntry],
        request_id: str,
    ) -> dict[str, Any]:
        """Import checklist rows for the current projected PR of a lane."""
        if not _private_local_request(ctx):
            return err("forbidden", "checklist import requires the private local listener")
        return await execute_command(
            ctx,
            "checklist_import",
            request_id,
            {"lane": lane, "rows": [row.model_dump() for row in rows]},
        )

    @server.tool()
    async def checklist_done(  # pyright: ignore[reportUnusedFunction]
        ctx: Context[Any, Request],
        lane: str,
        step: str,
        request_id: str,
        ref: str | None = None,
    ) -> dict[str, Any]:
        """Complete an active checklist step. With no ref, use the step's declared ref."""
        args = {"lane": lane, "step": step}
        if ref is not None:
            args["ref"] = ref
        return await execute_command(ctx, "checklist_done", request_id, args)

    @server.tool()
    async def freeze(  # pyright: ignore[reportUnusedFunction]
        ctx: Context[Any, Request], reason: str, request_id: str
    ) -> dict[str, Any]:
        """Set this project's deploy window and give the reason shown by the merge gate."""
        return await execute_command(ctx, "freeze", request_id, {"reason": reason})

    @server.tool()
    async def unfreeze(  # pyright: ignore[reportUnusedFunction]
        ctx: Context[Any, Request], request_id: str
    ) -> dict[str, Any]:
        """Clear this project's deploy window."""
        return await execute_command(ctx, "unfreeze", request_id, {})

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

    @server.tool()
    async def route(  # pyright: ignore[reportUnusedFunction]
        ctx: Context[Any, Request], action_kind: str, lane: str, request_id: str
    ) -> dict[str, Any]:
        """Assign a lane to a hands-on action kind."""
        return await execute_command(
            ctx, "route", request_id, {"action_kind": action_kind, "lane": lane}
        )

    @server.tool()
    async def route_request(  # pyright: ignore[reportUnusedFunction]
        ctx: Context[Any, Request], action_kind: str, request_id: str
    ) -> dict[str, Any]:
        """Return the assigned lane or raise a next item for the missing route."""
        return await execute_command(ctx, "route_request", request_id, {"action_kind": action_kind})

    @server.tool()
    async def budget_open(  # pyright: ignore[reportUnusedFunction]
        ctx: Context[Any, Request],
        run: str,
        lane: str,
        cap_usd: StrictFloat,
        provider: str,
        key_scope: str,
        request_id: str,
    ) -> dict[str, Any]:
        """Register a run's spend cap and owning lane. Supply only the name of the key scope."""
        return await execute_command(
            ctx,
            "budget_open",
            request_id,
            {
                "run": run,
                "lane": lane,
                "cap_usd": cap_usd,
                "provider": provider,
                "key_scope": key_scope,
            },
        )

    @server.tool()
    async def budget_status(  # pyright: ignore[reportUnusedFunction]
        ctx: Context[Any, Request], run: str | None = None
    ) -> dict[str, Any]:
        """Show current cumulative spend against caps, optionally for one run."""
        try:
            caller = board_access(ctx)
            if caller.ledger is None:
                raise LedgerError("unavailable", "the project ledger is unavailable")
            return ok({"budgets": await caller.ledger.budget_status(caller.identity.role, run)})
        except LedgerError as exc:
            return err(exc.code, exc.message)

    @server.tool()
    async def run_report(  # pyright: ignore[reportUnusedFunction]
        ctx: Context[Any, Request], lane: str, run_id: str, state: str, request_id: str
    ) -> dict[str, Any]:
        """Record an observation of a run owned by the calling worker's lane."""
        return await execute_command(
            ctx, "run_report", request_id, {"lane": lane, "run_id": run_id, "state": state}
        )

    @server.tool()
    async def decide(  # pyright: ignore[reportUnusedFunction]
        ctx: Context[Any, Request],
        who: str,
        exact_words: str,
        where_ref: DecisionCitation,
        governs: list[str],
        settles: list[str],
        request_id: str,
    ) -> dict[str, Any]:
        """Record the human's verbatim decision and the tickets and asks it applies to.

        Cite a projected comment with kind github_comment, repository, issue, and comment_id.
        Cite a chat source with kind chat and its opaque ref.
        """
        return await execute_command(
            ctx,
            "decide",
            request_id,
            {
                "who": who,
                "exact_words": exact_words,
                "where_ref": where_ref.model_dump(),
                "governs": governs,
                "settles": settles,
            },
        )

    @server.tool()
    async def decisions(  # pyright: ignore[reportUnusedFunction]
        ctx: Context[Any, Request], governs: str | None = None
    ) -> dict[str, Any]:
        """List recorded decisions, optionally filtered by one exact governed ticket reference."""
        try:
            caller = board_access(ctx)
            if caller.ledger is None:
                raise LedgerError("unavailable", "the project ledger is unavailable")
            return ok(
                {"decisions": await caller.ledger.list_decisions(caller.identity.role, governs)}
            )
        except LedgerError as exc:
            return err(exc.code, exc.message)

    @server.tool()
    async def ask(  # pyright: ignore[reportUnusedFunction]
        ctx: Context[Any, Request],
        issue: StrictInt,
        text: str,
        options: list[str],
        comment_id: StrictInt,
        request_id: str,
    ) -> dict[str, Any]:
        """Record an ask citing an existing projected comment on a GitHub issue."""
        return await execute_command(
            ctx,
            "ask",
            request_id,
            {"issue": issue, "text": text, "options": options, "comment_id": comment_id},
        )

    @server.tool()
    async def asks(  # pyright: ignore[reportUnusedFunction]
        ctx: Context[Any, Request], stale_only: bool = False
    ) -> dict[str, Any]:
        """List recorded asks, optionally limiting results to active stale conditions."""
        try:
            caller = board_access(ctx)
            if caller.ledger is None:
                raise LedgerError("unavailable", "the project ledger is unavailable")
            return ok({"asks": await caller.ledger.list_asks(caller.identity.role, stale_only)})
        except LedgerError as exc:
            return err(exc.code, exc.message)

    @server.tool()
    async def trigger(  # pyright: ignore[reportUnusedFunction]
        ctx: Context[Any, Request],
        predicate: dict[str, Any],
        action: dict[str, Any],
        request_id: str,
        lane: str | None = None,
        mode: Literal["once", "every"] = "once",
        imported_fired: StrictBool = False,
    ) -> dict[str, Any]:
        """Register a once or every trigger. A predicate can be {"pr_merged":{"number":42}} or use
        branch_glob instead of number. all_of and any_of take arrays of predicates.
        Use {"checklist_empty":{"lane":"name"}} to react when the last active step closes.
        An action object names one of notify_orchestrator (text), send (worker, text),
        mark (lane, state), or propose_archive (lane). On the private local listener,
        imported_fired records a previously fired once trigger without delivering it.
        """
        args: dict[str, Any] = {"predicate": predicate, "action": action}
        if lane is not None:
            args["lane"] = lane
        if mode != "once":
            args["mode"] = mode
        if imported_fired:
            if not _private_local_request(ctx):
                return err("forbidden", "imported_fired requires the private local listener")
            args["imported_fired"] = True
        return await execute_command(ctx, "trigger", request_id, args)

    @server.tool()
    async def triggers(  # pyright: ignore[reportUnusedFunction]
        ctx: Context[Any, Request], lane: str | None = None
    ) -> dict[str, Any]:
        """List trigger states and bindings, optionally for a single lane."""
        try:
            caller = board_access(ctx)
            if caller.ledger is None:
                raise LedgerError("unavailable", "the project ledger is unavailable")
            return ok({"triggers": await caller.ledger.list_triggers(caller.identity.role, lane)})
        except LedgerError as exc:
            return err(exc.code, exc.message)

    @server.tool()
    async def cancel(  # pyright: ignore[reportUnusedFunction]
        ctx: Context[Any, Request], trigger_id: str, request_id: str
    ) -> dict[str, Any]:
        """Cancel a trigger before it fires."""
        return await execute_command(ctx, "cancel", request_id, {"trigger_id": trigger_id})

    @server.tool()
    async def outbox_lease(  # pyright: ignore[reportUnusedFunction]
        ctx: Context[Any, Request], request_id: str, consumer_id: str
    ) -> dict[str, Any]:
        """Lease the next Claude outbox message. Generate a UUIDv4 consumer ID per drain process.

        Use a fresh request ID for new work. A retry from the same consumer returns its original
        lease and expiry. Deliver only while the lease is current.
        """
        try:
            scoped_request = lease_request_id(consumer_id, request_id)
        except LedgerError as exc:
            return err(exc.code, exc.message)
        return await execute_command(ctx, "outbox_lease", scoped_request, {})

    @server.tool()
    async def outbox_done(  # pyright: ignore[reportUnusedFunction]
        ctx: Context[Any, Request], act_id: str, request_id: str
    ) -> dict[str, Any]:
        """Complete the current lease after delivery. Pass its opaque act_id."""
        return await execute_command(ctx, "outbox_done", request_id, {"act_id": act_id})

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
