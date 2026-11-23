"""The stdio proxy behind ``plotroom connect --stdio`` (AD-7).

It forwards each JSON-RPC message body unchanged as one HTTP POST to the daemon and derives the
HTTP envelope from the message: the protocol-version, method and name headers in the 2026 era, and
the negotiated version after ``initialize`` in the handshake era. Calls run concurrently and SSE
responses are relayed as they arrive. Stdout carries protocol only; logs go to stderr.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import re
import sys
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, BinaryIO, cast
from urllib.parse import urlsplit, urlunsplit

import httpx
from mcp.shared.inbound import NAME_BEARING_METHODS, encode_header_value
from mcp_types import PROTOCOL_VERSION_META_KEY

log = logging.getLogger("plotroom.proxy")

JSONRPC_INTERNAL_ERROR = -32603
LISTEN_METHOD = "subscriptions/listen"  # 2026 era: a request whose stream ends only when canceled
DEFAULT_MAX_PENDING = 256
_BASE_HEADERS = {
    "content-type": "application/json",
    "accept": "application/json, text/event-stream",
}
_BEARER = re.compile(r"[A-Za-z0-9._~+/-]+=*")


class UpstreamStartError(Exception):
    """The server the proxy fronts is not running and could not be started; the text says why."""


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
        ensure: Callable[[], Awaitable[str | None]] | None = None,
        bearer: str | None = None,
        current_url: Callable[[], Awaitable[str | None]] | None = None,
        host_alias: str | None = None,
    ) -> None:
        if bearer is not None and not _BEARER.fullmatch(bearer):
            raise EndpointError("invalid bearer token")
        check_endpoint(url, credentialed=bearer is not None)
        self._url = url
        self._bearer = bearer
        self._shown_url = _display_url(url)
        self._max_pending = max_pending
        self._tasks: set[asyncio.Task[None]] = set()
        self._orphaned_writes: set[asyncio.Future[None]] = set()
        self._stdin = stdin if stdin is not None else sys.stdin.buffer
        self._stdout = stdout if stdout is not None else sys.stdout.buffer
        self._http = http
        self._write_lock = asyncio.Lock()
        self._inflight: dict[Any, asyncio.Task[None]] = {}
        self._streams: set[asyncio.Task[None]] = set()
        self._gone = False
        self._negotiated: str | None = None
        self._ensure = ensure
        self._current_url = current_url
        self._host_alias = host_alias
        self._starting: asyncio.Task[str | None] | None = None
        self._ready = ensure is None

    async def run(self) -> None:
        """Read messages until stdin closes, then let in-flight calls finish."""
        owned = self._http is None
        http = self._http or httpx.AsyncClient(
            timeout=httpx.Timeout(10.0, read=None), trust_env=self._bearer is None
        )
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
                if self._gone:
                    break
                if self._over_limit(message):  # pyright: ignore[reportUnknownArgumentType]
                    self._reject_busy(message)  # pyright: ignore[reportUnknownArgumentType]
                    continue
                task = self._dispatch(http, body, message)  # pyright: ignore[reportUnknownArgumentType]
                if task is not None:
                    tasks.add(task)
                    task.add_done_callback(tasks.discard)
            # stdin closed: the client is shutting down. A listen stream has no end of its own, so
            # it would hold this process open forever; every other call may still be answered.
            for stream in self._streams:
                stream.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
        finally:
            if owned:
                await http.aclose()

    def _over_limit(self, message: Mapping[str, Any]) -> bool:
        """Too much work is pending; a cancel still gets through, since it frees work."""
        return (
            message.get("method") != "notifications/cancelled"
            and len(self._tasks) + len(self._orphaned_writes) >= self._max_pending
        )

    def _reject_busy(self, message: Mapping[str, Any]) -> None:
        """Answer an over-limit request without waiting on stdout, which may be what is stuck."""
        request_id = message.get("id")
        log.error("too many pending requests; dropped one")
        answerable = (
            message.get("method") is not None and _valid_id(request_id) and request_id is not None
        )
        if answerable and len(self._tasks) + len(self._orphaned_writes) < 2 * self._max_pending:
            refusal = asyncio.ensure_future(
                self._emit(
                    _error_response(request_id, "plotroom proxy has too many pending requests")
                )
            )
            self._tasks.add(refusal)
            refusal.add_done_callback(self._tasks.discard)

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
            if target is not None and _valid_id(target) and (pending := self._inflight.get(target)):
                pending.cancel()
            return None
        task = asyncio.ensure_future(self._forward(http, body, message))
        if method == LISTEN_METHOD:
            self._streams.add(task)
            task.add_done_callback(self._streams.discard)
        if method is not None and request_id is not None:
            self._inflight[request_id] = task

            def forget(done: asyncio.Task[None], rid: Any = request_id) -> None:
                if self._inflight.get(rid) is done:  # a reused id may already name a newer task
                    del self._inflight[rid]

            task.add_done_callback(forget)
        return task

    async def _ensure_ready(self) -> asyncio.Task[str | None] | None:
        """Wait until the server is up, starting it once however many requests wait on it.

        A start that reports where the server listens (a daemon on a port of its own choosing) moves
        the proxy there. Returns the start this request relied on, so a refusal can tell whether
        that start is still the current one.
        """
        if self._ensure is None:
            return None
        if self._ready:
            previous = self._starting
            # A local bearer stays bound to the owner of its data directory, even if another
            # listener takes the port after that daemon exits.
            current = self._url if self._current_url is None else await self._current_url()
            if self._ready and current == self._url:
                return self._starting
            if self._starting is previous:
                self._ready = False
                self._starting = None
        starting = self._starting
        if starting is None or (starting.done() and starting.exception() is not None):
            starting = self._starting = asyncio.ensure_future(self._ensure())
        # Shielded: one canceled request must not abort the start the others are waiting on.
        url = await asyncio.shield(starting)
        if url is not None:
            check_endpoint(url, credentialed=self._bearer is not None)
            self._url = url
            self._shown_url = _display_url(url)
        self._ready = True
        return starting

    async def _forward(
        self,
        http: httpx.AsyncClient,
        body: bytes,
        message: dict[str, Any],
        *,
        retried: bool = False,
    ) -> None:
        request_id = message.get("id")
        is_request = message.get("method") is not None and request_id is not None
        answered = False
        relied_on: asyncio.Task[str | None] | None = None
        try:
            relied_on = await self._ensure_ready()
            headers = envelope_headers(message, self._negotiated)
            if self._bearer is not None:
                headers["authorization"] = f"Bearer {self._bearer}"
            if self._host_alias is not None:
                headers["host"] = f"{self._host_alias}:{urlsplit(self._url).port}"
            async with http.stream("POST", self._url, content=body, headers=headers) as response:
                content_type = response.headers.get("content-type", "")
                if "text/event-stream" in content_type:
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
                if is_request or response.status_code >= 400:  # a notification's 202 is expected
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
        except BrokenPipeError:
            raise
        except UpstreamStartError as exc:
            log.error("plotroom is not running: %s", exc)
            if is_request:
                await self._emit(_error_response(request_id, f"plotroom is not running: {exc}"))
        except httpx.ConnectError:
            if self._ensure is not None and not retried:
                # Refused before any byte was sent, so the daemon never saw it: start it and retry.
                # Several requests refused together share one restart: only the first one to get
                # here retires the start they all relied on.
                if self._starting is relied_on:
                    self._ready = False
                    self._starting = None
                await self._forward(http, body, message, retried=True)
                return
            log.error("cannot reach plotroom at %s: ConnectError", self._shown_url)
            if is_request:
                await self._emit(
                    _error_response(
                        request_id,
                        f"cannot reach plotroom at {self._shown_url}; run `plotroom serve`",
                    )
                )
        except httpx.HTTPError as exc:
            # the exception's text can repeat the full URL, so only its type is logged
            log.error("cannot reach plotroom at %s: %s", self._shown_url, type(exc).__name__)
            if is_request and not answered:  # a stream cut after its response needs no second one
                await self._emit(
                    _error_response(
                        request_id,
                        f"cannot reach plotroom at {self._shown_url}; run `plotroom serve`",
                    )
                )
        except Exception as exc:
            # e.g. a method name that cannot be an HTTP header: answer instead of leaving it hanging
            log.error("could not forward a message: %s", type(exc).__name__)
            if is_request and not answered:
                await self._emit(
                    _error_response(request_id, "the message cannot be forwarded to plotroom")
                )

    def _client_gone(self) -> None:
        """Nobody reads stdout any more: abort every call, so the server stops working for them."""
        self._gone = True
        for task in list(self._tasks):
            task.cancel()

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
        # The write it leaves behind still counts toward the pending limit until it finishes.
        write = asyncio.ensure_future(self._locked_write(payload))
        try:
            await asyncio.shield(write)
        except asyncio.CancelledError:
            if not write.done():
                self._orphaned_writes.add(write)
                write.add_done_callback(self._orphaned_writes.discard)
            raise

    async def _locked_write(self, payload: bytes) -> None:
        async with self._write_lock:
            try:
                await asyncio.to_thread(self._write_blocking, payload)
            except BrokenPipeError:
                log.error("stdout closed; the client is gone")
                self._client_gone()
                raise

    def _write_blocking(self, payload: bytes) -> None:
        """Runs off the event loop: a client that stops reading must not stall forwarding."""
        self._stdout.write(payload + b"\n")
        self._stdout.flush()


class EndpointError(ValueError):
    """The server URL cannot be used safely."""


def check_endpoint(url: str, *, credentialed: bool = False) -> None:
    """Refuse non-HTTP endpoints and require HTTPS for remote credentials."""
    try:
        parts = urlsplit(url)
        _ = parts.port
    except ValueError as exc:
        raise EndpointError("the endpoint URL has an invalid port") from exc
    host = parts.hostname
    if parts.scheme not in ("http", "https") or not host:
        raise EndpointError("the endpoint must be an http or https URL with a host")
    if credentialed and parts.username is not None:
        raise EndpointError("the endpoint cannot contain user info when a bearer token is supplied")
    carries_secret = credentialed or bool(parts.username or parts.password or parts.query)
    if parts.scheme == "http" and carries_secret and not _is_loopback(host):
        raise EndpointError(
            "refusing to send a credential in a plaintext http URL to a non-local host; use https"
        )


def _is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


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
