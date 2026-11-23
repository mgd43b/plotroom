"""The MCP tool surface on stateless streamable HTTP (AD-7)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette

from plotroom.core.snapshot import snapshot_result


def build_app(
    project_ids: tuple[str, ...],
    allowed_hosts: Sequence[str],
    *,
    unavailable: str | None = None,
) -> Starlette:
    """The ASGI app serving the tool set at ``/mcp``.

    ``unavailable`` is set when the server is up but cannot honor calls (SQLite below the floor);
    every tool then answers ``unavailable``.
    """
    server = MCPServer("plotroom")

    @server.tool()
    def snapshot() -> dict[str, Any]:  # pyright: ignore[reportUnusedFunction]
        """Summarize the whole board: lanes, open items by kind, and the ranked item list."""
        return snapshot_result(project_ids, unavailable)

    return server.streamable_http_app(
        stateless_http=True,
        transport_security=TransportSecuritySettings(allowed_hosts=list(allowed_hosts)),
    )
