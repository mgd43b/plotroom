"""The stdio proxy behind ``plotroom connect --stdio`` (AD-7).

It forwards each JSON-RPC message body unchanged as one HTTP POST to the daemon and derives the
HTTP envelope from the message: the protocol-version, method and name headers in the 2026 era, and
the negotiated version after ``initialize`` in the handshake era. Calls run concurrently and SSE
responses are relayed as they arrive. Stdout carries protocol only; logs go to stderr.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from collections.abc import Mapping
from typing import Any, BinaryIO, cast
from urllib.parse import urlsplit, urlunsplit

import httpx
from mcp.shared.inbound import NAME_BEARING_METHODS, encode_header_value
from mcp_types import PROTOCOL_VERSION_META_KEY

log = logging.getLogger("plotroom.proxy")

JSONRPC_INTERNAL_ERROR = -32603
DEFAULT_MAX_PENDING = 256
_BASE_HEADERS = {
    "content-type": "application/json",
    "accept": "application/json, text/event-stream",
}


def envelope_headers(message: Mapping[str, Any], negotiated: str | None) -> dict[str, str]:
    """The HTTP headers for one outgoing message.

    A message whose ``params._meta`` carries a protocol version is in the 2026 era: the headers
    mirror it. Otherwise it is in the handshake era: ``initialize`` carries no version header, and
    every later message carries the version that ``initialize`` negotiated.
    """
    headers = dict(_BASE_HEADERS)
    params: Any = message.get("params")
    meta: Any = params.get("_meta") if isinstance(params, dict) else None  # pyright: ignore[reportUnknownVariableType, reportUnknownMemberType]
    version: Any = meta.get(PROTOCOL_VERSION_META_KEY) if isinstance(meta, dict) else None  # pyright: ignore[reportUnknownVariableType, reportUnknownMemberType]
    method = message.get("method")
    if isinstance(version, str) and isinstance(method, str):
        headers["mcp-protocol-version"] = version
        headers["mcp-method"] = method
        name_key = NAME_BEARING_METHODS.get(method)
        if name_key is not None and isinstance(params, dict):
            name: Any = params.get(name_key)  # pyright: ignore[reportUnknownVariableType, reportUnknownMemberType]
            if isinstance(name, str):
                headers["mcp-name"] = encode_header_value(name)
    elif method != "initialize" and negotiated is not None:
        headers["mcp-protocol-version"] = negotiated
    return headers


def _error_response(request_id: Any, message: str) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": JSONRPC_INTERNAL_ERROR, "message": message},
    }


class StdioProxy:
    """Relays newline-delimited JSON-RPC between a client's stdio and the daemon's HTTP endpoint."""

    def __init__(
        self,
        url: str,
        *,
        stdin: BinaryIO | None = None,
        stdout: BinaryIO | None = None,
        http: httpx.AsyncClient | None = None,
        max_pending: int = DEFAULT_MAX_PENDING,
    ) -> None:
        self._url = url
        self._shown_url = _display_url(url)
        self._max_pending = max_pending
        self._tasks: set[asyncio.Task[None]] = set()
        self._stdin = stdin if stdin is not None else sys.stdin.buffer
        self._stdout = stdout if stdout is not None else sys.stdout.buffer
        self._http = http
        self._write_lock = asyncio.Lock()
        self._inflight: dict[Any, asyncio.Task[None]] = {}
        self._negotiated: str | None = None

    async def run(self) -> None:
        """Read messages until stdin closes, then let in-flight calls finish."""
        owned = self._http is None
        http = self._http or httpx.AsyncClient(timeout=httpx.Timeout(10.0, read=None))
        tasks = self._tasks
        try:
            while line := await asyncio.to_thread(self._stdin.readline):
                body = line.strip()
                if not body:
                    continue
                try:
                    message: Any = json.loads(body)
                except ValueError:  # bad JSON or invalid UTF-8
                    log.error("dropped a line that is not JSON")
                    continue
                if not isinstance(message, dict):
                    log.error("dropped a message that is not a JSON object")
                    continue
                if self._over_limit(message):  # pyright: ignore[reportUnknownArgumentType]
                    await self._reject_busy(message)  # pyright: ignore[reportUnknownArgumentType]
                    continue
                task = self._dispatch(http, body, message)  # pyright: ignore[reportUnknownArgumentType]
                if task is not None:
                    tasks.add(task)
                    task.add_done_callback(tasks.discard)
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
        finally:
            if owned:
                await http.aclose()

    def _over_limit(self, message: Mapping[str, Any]) -> bool:
        """Too much work is pending; a cancel still gets through, since it frees work."""
        return (
            message.get("method") != "notifications/cancelled"
            and len(self._tasks) >= self._max_pending
        )

    async def _reject_busy(self, message: Mapping[str, Any]) -> None:
        request_id = message.get("id")
        log.error("too many pending requests; dropped one")
        if message.get("method") is not None and _valid_id(request_id) and request_id is not None:
            await self._emit(
                _error_response(request_id, "plotroom proxy has too many pending requests")
            )

    def _dispatch(
        self, http: httpx.AsyncClient, body: bytes, message: dict[str, Any]
    ) -> asyncio.Task[None] | None:
        method = message.get("method")
        request_id = message.get("id")
        if not _valid_id(request_id):
            log.error("dropped a message whose id is not a string or an integer")
            return None
        if method == "notifications/cancelled":
            # Stateless HTTP has no session to forward a cancel to; aborting the request's own
            # POST is the signal (a request the server already answered has nothing to cancel).
            params: Any = message.get("params")
            target: Any = params.get("requestId") if isinstance(params, dict) else None  # pyright: ignore[reportUnknownVariableType, reportUnknownMemberType]
            if isinstance(target, str | int) and (pending := self._inflight.get(target)):
                pending.cancel()
            return None
        task = asyncio.ensure_future(self._forward(http, body, message))
        if method is not None and request_id is not None:
            self._inflight[request_id] = task

            def forget(done: asyncio.Task[None], rid: Any = request_id) -> None:
                if self._inflight.get(rid) is done:  # a reused id may already name a newer task
                    del self._inflight[rid]

            task.add_done_callback(forget)
        return task

    async def _forward(self, http: httpx.AsyncClient, body: bytes, message: dict[str, Any]) -> None:
        request_id = message.get("id")
        is_request = message.get("method") is not None and request_id is not None
        headers = envelope_headers(message, self._negotiated)
        try:
            async with http.stream("POST", self._url, content=body, headers=headers) as response:
                content_type = response.headers.get("content-type", "")
                if "text/event-stream" in content_type:
                    answered = False
                    async for payload in _sse_payloads(response):
                        relayed = await self._relay(payload, message)
                        answered = answered or (relayed and _answers(payload, request_id))
                    if is_request and not answered:
                        log.error("event stream ended without a response to request %s", request_id)
                        await self._emit(
                            _error_response(
                                request_id, "plotroom server ended the stream without a response"
                            )
                        )
                    return
                raw = await response.aread()
                if raw.strip() and await self._relay(raw.strip(), message):
                    return
                log.error("daemon answered HTTP %s with no JSON-RPC body", response.status_code)
                if is_request:
                    await self._emit(
                        _error_response(
                            request_id,
                            f"plotroom server answered HTTP {response.status_code} without a reply",
                        )
                    )
        except asyncio.CancelledError:
            raise
        except httpx.HTTPError as exc:
            # the exception's text can repeat the full URL, so only its type is logged
            log.error("cannot reach plotroom at %s: %s", self._shown_url, type(exc).__name__)
            if is_request:
                await self._emit(
                    _error_response(
                        request_id,
                        f"cannot reach plotroom at {self._shown_url}; run `plotroom serve`",
                    )
                )

    async def _relay(self, payload: bytes, request: Mapping[str, Any]) -> bool:
        """Write one server message to stdout; ``False`` when it is not JSON-RPC."""
        try:
            decoded: Any = json.loads(payload)
        except ValueError:
            return False
        if not isinstance(decoded, dict) or "jsonrpc" not in decoded:
            return False
        if request.get("method") == "initialize":
            result: Any = decoded.get("result")  # pyright: ignore[reportUnknownVariableType, reportUnknownMemberType]
            version: Any = result.get("protocolVersion") if isinstance(result, dict) else None  # pyright: ignore[reportUnknownVariableType, reportUnknownMemberType]
            if isinstance(version, str):
                self._negotiated = version
        await self._emit(cast("dict[str, Any]", decoded))  # one line, whatever the server sent
        return True

    async def _emit(self, message: dict[str, Any]) -> None:
        await self._write(json.dumps(message, separators=(",", ":")).encode())

    async def _write(self, payload: bytes) -> None:
        # Shielded: a cancelled caller must not release the lock while the thread still writes.
        await asyncio.shield(self._locked_write(payload))

    async def _locked_write(self, payload: bytes) -> None:
        async with self._write_lock:
            try:
                await asyncio.to_thread(self._write_blocking, payload)
            except BrokenPipeError:
                log.error("stdout closed; the client is gone")
                raise

    def _write_blocking(self, payload: bytes) -> None:
        """Runs off the event loop: a client that stops reading must not stall forwarding."""
        self._stdout.write(payload + b"\n")
        self._stdout.flush()


def _valid_id(request_id: Any) -> bool:
    """JSON-RPC ids are strings, integers or absent; anything else cannot be tracked."""
    return request_id is None or (
        isinstance(request_id, str | int) and not isinstance(request_id, bool)
    )


def _display_url(url: str) -> str:
    """The endpoint without userinfo, query or fragment, for logs and error messages."""
    parts = urlsplit(url)
    host = parts.hostname or ""
    host = f"[{host}]" if ":" in host else host
    netloc = f"{host}:{parts.port}" if parts.port else host
    return urlunsplit((parts.scheme, netloc, parts.path, "", ""))


def _answers(payload: bytes, request_id: Any) -> bool:
    """Whether the payload is the response (result or error) to ``request_id``."""
    try:
        decoded: Any = json.loads(payload)
    except ValueError:
        return False
    if not isinstance(decoded, dict):
        return False
    body = cast("dict[str, Any]", decoded)
    return body.get("id") == request_id and ("result" in body or "error" in body)


async def _sse_payloads(response: httpx.Response):
    """Each complete SSE event's ``data`` as bytes."""
    data: list[str] = []
    async for line in response.aiter_lines():
        if line == "":
            if data:
                yield "\n".join(data).encode()
                data = []
        elif line.startswith("data:"):
            data.append(line[5:].removeprefix(" "))
    if data:
        yield "\n".join(data).encode()
