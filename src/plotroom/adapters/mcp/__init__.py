"""MCP adapter: the tool surface over stateless HTTP and the stdio proxy."""

from plotroom.adapters.mcp.server import build_app

__all__ = ["build_app"]
