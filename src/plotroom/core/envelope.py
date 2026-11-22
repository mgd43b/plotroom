"""The tool result envelope: ``{ok, data}`` or ``{ok: false, error: {code, message}}``."""

from __future__ import annotations

from typing import Any, Literal, get_args

ErrorCode = Literal["forbidden", "not_found", "conflict", "invalid", "unavailable"]
ERROR_CODES: tuple[str, ...] = get_args(ErrorCode)


def ok(data: Any) -> dict[str, Any]:
    """Wrap a successful result."""
    return {"ok": True, "data": data}


def err(code: ErrorCode, message: str) -> dict[str, Any]:
    """Wrap a failure; ``code`` must be one of the declared codes."""
    if code not in ERROR_CODES:
        raise ValueError(f"unknown error code {code!r}; expected one of {ERROR_CODES}")
    return {"ok": False, "error": {"code": code, "message": message}}
