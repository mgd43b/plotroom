"""The stdio proxy against a scripted HTTP peer: headers, SSE, concurrency, cancel, failures."""

from __future__ import annotations

import asyncio
import io
import json
import socket
import threading
from typing import Any

import httpx
from mcp_types import PROTOCOL_VERSION_META_KEY

from plotroom.adapters.mcp.proxy import StdioProxy, envelope_headers

MODERN = "2026-07-28"


def request(
    method: str, request_id: int | None, params: dict[str, Any] | None = None
) -> dict[str, Any]:
    message: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
    if request_id is not None:
        message["id"] = request_id
    if params is not None:
        message["params"] = params
    return message


def modern(method: str, request_id: int, params: dict[str, Any]) -> dict[str, Any]:
    return request(method, request_id, {**params, "_meta": {PROTOCOL_VERSION_META_KEY: MODERN}})


def lines(*messages: dict[str, Any]) -> io.BytesIO:
    return io.BytesIO(b"".join(json.dumps(m).encode() + b"\n" for m in messages))


class Output(io.BytesIO):
    """Collects stdout and tells a waiting test when each line lands."""

    def __init__(self) -> None:
        super().__init__()
        self.landed = threading.Event()

    def write(self, data: Any) -> int:
        written = super().write(data)
        self.landed.set()
        return written

    def messages(self) -> list[dict[str, Any]]:
        return [json.loads(line) for line in self.getvalue().splitlines()]


def result_for(message: dict[str, Any]) -> bytes:
    return json.dumps({"jsonrpc": "2.0", "id": message["id"], "result": {}}).encode()


def client_for(handler: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


# --- header derivation ---------------------------------------------------------------------


def test_modern_request_headers_mirror_the_envelope() -> None:
    headers = envelope_headers(modern("tools/call", 1, {"name": "snapshot"}), None)
    assert headers["mcp-protocol-version"] == MODERN
    assert headers["mcp-method"] == "tools/call"
    assert headers["mcp-name"] == "snapshot"
    assert headers["accept"] == "application/json, text/event-stream"


def test_modern_request_without_a_name_has_no_name_header() -> None:
    headers = envelope_headers(modern("tools/list", 1, {}), None)
    assert headers["mcp-method"] == "tools/list"
    assert "mcp-name" not in headers


def test_initialize_carries_no_version_header() -> None:
    headers = envelope_headers(request("initialize", 1, {"protocolVersion": "2025-11-25"}), None)
    assert "mcp-protocol-version" not in headers
    assert "mcp-method" not in headers


def test_handshake_era_messages_carry_the_negotiated_version() -> None:
    for message in (
        request("tools/call", 2, {"name": "snapshot"}),
        request("notifications/initialized", None),
    ):
        assert envelope_headers(message, "2025-11-25")["mcp-protocol-version"] == "2025-11-25"
        assert "mcp-method" not in envelope_headers(message, "2025-11-25")


def test_before_negotiation_a_handshake_era_message_has_no_version_header() -> None:
    assert "mcp-protocol-version" not in envelope_headers(request("tools/list", 2, {}), None)


# --- relaying ------------------------------------------------------------------------------


async def test_negotiated_version_follows_initialize() -> None:
    sent: list[httpx.Request] = []
    stdout = Output()

    def handler(http_request: httpx.Request) -> httpx.Response:
        sent.append(http_request)
        body = json.loads(http_request.content)
        if body["method"] == "initialize":
            result: dict[str, Any] = {"protocolVersion": "2025-11-25", "capabilities": {}}
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": result})
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": {}})

    class Stdin:
        """Sends the second message only after the first one's answer reached stdout."""

        def __init__(self) -> None:
            self._script = [
                request("initialize", 1, {"protocolVersion": "2025-11-25"}),
                request("tools/list", 2, {}),
            ]

        def readline(self) -> bytes:
            if not self._script:
                return b""
            if len(self._script) == 1:
                stdout.landed.wait(timeout=30)
            return json.dumps(self._script.pop(0)).encode() + b"\n"

    proxy = StdioProxy("http://daemon/mcp", stdin=Stdin(), stdout=stdout, http=client_for(handler))  # pyright: ignore[reportArgumentType]
    await asyncio.wait_for(proxy.run(), timeout=30)
    assert [m["id"] for m in stdout.messages()] == [1, 2]
    assert "mcp-protocol-version" not in sent[0].headers
    assert sent[1].headers["mcp-protocol-version"] == "2025-11-25"
    assert json.loads(sent[1].content)["method"] == "tools/list"


async def test_bodies_are_forwarded_unchanged() -> None:
    sent: list[bytes] = []
    stdout = Output()
    original = (
        b'{"jsonrpc": "2.0",  "id": 7, "method": "tools/list",\t"params": {"x": "\xc3\xa9"}}\n'
    )

    def handler(http_request: httpx.Request) -> httpx.Response:
        sent.append(http_request.content)
        return httpx.Response(
            200, content=result_for({"id": 7}), headers={"content-type": "application/json"}
        )

    proxy = StdioProxy(
        "http://daemon/mcp", stdin=io.BytesIO(original), stdout=stdout, http=client_for(handler)
    )
    await asyncio.wait_for(proxy.run(), timeout=30)
    assert sent == [original.strip()]


async def test_sse_events_are_relayed_in_order() -> None:
    stdout = Output()
    progress = {"jsonrpc": "2.0", "method": "notifications/progress", "params": {"progress": 1}}
    final = {"jsonrpc": "2.0", "id": 3, "result": {"ok": True}}
    sse = f"event: message\ndata: {json.dumps(progress)}\n\nevent: message\ndata: {json.dumps(final)}\n\n"

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, content=sse.encode(), headers={"content-type": "text/event-stream"}
        )

    proxy = StdioProxy(
        "http://daemon/mcp",
        stdin=lines(modern("tools/call", 3, {"name": "snapshot"})),
        stdout=stdout,
        http=client_for(handler),
    )
    await asyncio.wait_for(proxy.run(), timeout=30)
    assert stdout.messages() == [progress, final]


async def test_calls_run_concurrently() -> None:
    """The first call is held until the second has been answered, so serial forwarding deadlocks."""
    second_answered = asyncio.Event()
    stdout = Output()

    async def handler(http_request: httpx.Request) -> httpx.Response:
        body = json.loads(http_request.content)
        if body["id"] == 1:
            await second_answered.wait()
        else:
            second_answered.set()
        return httpx.Response(
            200, content=result_for(body), headers={"content-type": "application/json"}
        )

    proxy = StdioProxy(
        "http://daemon/mcp",
        stdin=lines(modern("tools/list", 1, {}), modern("tools/list", 2, {})),
        stdout=stdout,
        http=client_for(handler),
    )
    await asyncio.wait_for(proxy.run(), timeout=30)
    assert [m["id"] for m in stdout.messages()] == [2, 1]


async def test_cancelling_a_request_aborts_it_and_forwards_nothing() -> None:
    never = asyncio.Event()
    stdout = Output()
    seen: list[str] = []

    async def handler(http_request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(http_request.content)["method"])
        await never.wait()
        return httpx.Response(200)

    cancel = request("notifications/cancelled", None, {"requestId": 5})
    proxy = StdioProxy(
        "http://daemon/mcp",
        stdin=lines(modern("tools/call", 5, {"name": "snapshot"}), cancel),
        stdout=stdout,
        http=client_for(handler),
    )
    await asyncio.wait_for(proxy.run(), timeout=30)
    assert stdout.messages() == []
    assert "notifications/cancelled" not in seen


# --- failures ------------------------------------------------------------------------------


def closed_port_url() -> str:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    return f"http://127.0.0.1:{port}/mcp"


async def test_an_unreachable_server_answers_the_request_with_an_error() -> None:
    stdout = Output()
    proxy = StdioProxy(closed_port_url(), stdin=lines(modern("tools/list", 9, {})), stdout=stdout)
    await asyncio.wait_for(proxy.run(), timeout=60)
    [answer] = stdout.messages()
    assert answer["id"] == 9
    assert answer["error"]["code"] == -32603
    assert "plotroom serve" in answer["error"]["message"]


async def test_an_unreachable_server_is_silent_for_notifications() -> None:
    stdout = Output()
    proxy = StdioProxy(
        closed_port_url(), stdin=lines(request("notifications/initialized", None)), stdout=stdout
    )
    await asyncio.wait_for(proxy.run(), timeout=60)
    assert stdout.messages() == []


async def test_a_non_json_http_error_becomes_an_error_for_the_request() -> None:
    stdout = Output()

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(421, text="Invalid Host header")

    proxy = StdioProxy(
        "http://daemon/mcp",
        stdin=lines(modern("tools/list", 4, {})),
        stdout=stdout,
        http=client_for(handler),
    )
    await asyncio.wait_for(proxy.run(), timeout=30)
    [answer] = stdout.messages()
    assert answer["id"] == 4
    assert "HTTP 421" in answer["error"]["message"]


async def test_a_jsonrpc_error_body_on_a_4xx_is_relayed_as_is() -> None:
    stdout = Output()
    error = {"jsonrpc": "2.0", "id": 4, "error": {"code": -32001, "message": "mismatch"}}

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json=error)

    proxy = StdioProxy(
        "http://daemon/mcp",
        stdin=lines(modern("tools/list", 4, {})),
        stdout=stdout,
        http=client_for(handler),
    )
    await asyncio.wait_for(proxy.run(), timeout=30)
    assert stdout.messages() == [error]


async def test_garbage_lines_are_dropped_and_the_proxy_keeps_going() -> None:
    stdout = Output()

    def handler(http_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=result_for(json.loads(http_request.content)),
            headers={"content-type": "application/json"},
        )

    stdin = io.BytesIO(b"not json\n[1, 2]\n" + lines(modern("tools/list", 8, {})).getvalue())
    proxy = StdioProxy("http://daemon/mcp", stdin=stdin, stdout=stdout, http=client_for(handler))
    await asyncio.wait_for(proxy.run(), timeout=30)
    assert [m["id"] for m in stdout.messages()] == [8]


async def test_malformed_cancel_notifications_do_not_kill_the_proxy() -> None:
    stdout = Output()

    def handler(http_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=result_for(json.loads(http_request.content)),
            headers={"content-type": "application/json"},
        )

    stdin = lines(
        {"jsonrpc": "2.0", "method": "notifications/cancelled", "params": None},
        request("notifications/cancelled", None, {"requestId": [1]}),
        request("notifications/cancelled", None, {"requestId": {"a": 1}}),
        modern("tools/list", 6, {}),
    )
    proxy = StdioProxy("http://daemon/mcp", stdin=stdin, stdout=stdout, http=client_for(handler))
    await asyncio.wait_for(proxy.run(), timeout=30)
    assert [m["id"] for m in stdout.messages()] == [6]


async def test_invalid_utf8_on_stdin_is_dropped() -> None:
    stdout = Output()

    def handler(http_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=result_for(json.loads(http_request.content)),
            headers={"content-type": "application/json"},
        )

    stdin = io.BytesIO(b'{"id": "\xff\xfe"}\n' + lines(modern("tools/list", 2, {})).getvalue())
    proxy = StdioProxy("http://daemon/mcp", stdin=stdin, stdout=stdout, http=client_for(handler))
    await asyncio.wait_for(proxy.run(), timeout=30)
    assert [m["id"] for m in stdout.messages()] == [2]


async def test_an_event_stream_that_ends_without_a_response_answers_with_an_error() -> None:
    stdout = Output()
    progress = {"jsonrpc": "2.0", "method": "notifications/progress", "params": {"progress": 1}}

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=f"data: {json.dumps(progress)}\n\n".encode(),
            headers={"content-type": "text/event-stream"},
        )

    proxy = StdioProxy(
        "http://daemon/mcp",
        stdin=lines(modern("tools/call", 12, {"name": "snapshot"})),
        stdout=stdout,
        http=client_for(handler),
    )
    await asyncio.wait_for(proxy.run(), timeout=30)
    first, second = stdout.messages()
    assert first == progress
    assert second["id"] == 12
    assert "without a response" in second["error"]["message"]


async def test_a_reused_request_id_can_still_be_cancelled() -> None:
    """The first call finishing must not forget the second call that reused its id."""
    first_done = asyncio.Event()
    never = asyncio.Event()
    stdout = Output()

    async def handler(http_request: httpx.Request) -> httpx.Response:
        body = json.loads(http_request.content)
        if body["method"] == "tools/list":
            first_done.set()
            return httpx.Response(
                200, content=result_for(body), headers={"content-type": "application/json"}
            )
        await never.wait()
        return httpx.Response(200)

    class Stdin:
        def __init__(self) -> None:
            self._script = [
                modern("tools/list", 5, {}),
                modern("tools/call", 5, {"name": "snapshot"}),
                request("notifications/cancelled", None, {"requestId": 5}),
            ]

        def readline(self) -> bytes:
            if not self._script:
                return b""
            if len(self._script) == 2:  # the second request reuses id 5 after the first ended
                asyncio.run_coroutine_threadsafe(first_done.wait(), loop).result(timeout=30)
            return json.dumps(self._script.pop(0)).encode() + b"\n"

    loop = asyncio.get_running_loop()
    proxy = StdioProxy("http://daemon/mcp", stdin=Stdin(), stdout=stdout, http=client_for(handler))  # pyright: ignore[reportArgumentType]
    await asyncio.wait_for(proxy.run(), timeout=30)
    assert [m["id"] for m in stdout.messages()] == [5]  # only the first call answered


async def test_a_request_with_an_array_or_object_id_is_dropped_without_stopping_the_proxy() -> None:
    stdout = Output()

    def handler(http_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=result_for(json.loads(http_request.content)),
            headers={"content-type": "application/json"},
        )

    bad: list[dict[str, Any]] = [{"jsonrpc": "2.0", "id": [1], "method": "tools/list"}]
    bad.append({"jsonrpc": "2.0", "id": {"a": 1}, "method": "tools/list"})
    bad.append({"jsonrpc": "2.0", "id": True, "method": "tools/list"})
    proxy = StdioProxy(
        "http://daemon/mcp",
        stdin=lines(*bad, modern("tools/list", 3, {})),
        stdout=stdout,
        http=client_for(handler),
    )
    await asyncio.wait_for(proxy.run(), timeout=30)
    assert [m["id"] for m in stdout.messages()] == [3]


async def test_a_pretty_printed_or_multi_line_response_is_written_on_one_line() -> None:
    stdout = Output()
    pretty = json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"a": [1, 2]}}, indent=2)
    sse = 'data: {\ndata:   "jsonrpc": "2.0", "id": 2,\ndata:   "result": {}\ndata: }\n\n'

    def handler(http_request: httpx.Request) -> httpx.Response:
        if json.loads(http_request.content)["id"] == 1:
            return httpx.Response(
                200, content=pretty.encode(), headers={"content-type": "application/json"}
            )
        return httpx.Response(
            200, content=sse.encode(), headers={"content-type": "text/event-stream"}
        )

    proxy = StdioProxy(
        "http://daemon/mcp",
        stdin=lines(modern("tools/list", 1, {}), modern("tools/list", 2, {})),
        stdout=stdout,
        http=client_for(handler),
    )
    await asyncio.wait_for(proxy.run(), timeout=30)
    raw_lines = stdout.getvalue().splitlines()
    assert len(raw_lines) == 2
    assert sorted(json.loads(line)["id"] for line in raw_lines) == [1, 2]


async def test_an_empty_or_202_answer_to_a_request_becomes_an_error() -> None:
    stdout = Output()

    def handler(http_request: httpx.Request) -> httpx.Response:
        return httpx.Response(202 if json.loads(http_request.content)["id"] == 1 else 200)

    proxy = StdioProxy(
        "http://daemon/mcp",
        stdin=lines(modern("tools/list", 1, {}), modern("tools/list", 2, {})),
        stdout=stdout,
        http=client_for(handler),
    )
    await asyncio.wait_for(proxy.run(), timeout=30)
    answers = {m["id"]: m for m in stdout.messages()}
    assert set(answers) == {1, 2}
    assert all("without a reply" in a["error"]["message"] for a in answers.values())


def accepted(_: httpx.Request) -> httpx.Response:
    return httpx.Response(202)


async def test_an_empty_answer_to_a_notification_stays_silent() -> None:
    stdout = Output()
    proxy = StdioProxy(
        "http://daemon/mcp",
        stdin=lines(request("notifications/initialized", None)),
        stdout=stdout,
        http=client_for(accepted),
    )
    await asyncio.wait_for(proxy.run(), timeout=30)
    assert stdout.messages() == []


async def test_a_client_that_stops_reading_does_not_stall_the_event_loop() -> None:
    """The first write blocks until another coroutine on the same loop runs."""
    unblocked = threading.Event()
    loop_ran = asyncio.Event()

    class BlockingOutput(Output):
        def write(self, data: Any) -> int:
            assert unblocked.wait(timeout=30), "the event loop never got to run"
            return super().write(data)

    async def other_work() -> None:
        await asyncio.sleep(0)
        loop_ran.set()
        unblocked.set()

    stdout = BlockingOutput()

    def handler(http_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=result_for(json.loads(http_request.content)),
            headers={"content-type": "application/json"},
        )

    proxy = StdioProxy(
        "http://daemon/mcp",
        stdin=lines(modern("tools/list", 1, {})),
        stdout=stdout,
        http=client_for(handler),
    )
    await asyncio.wait_for(asyncio.gather(proxy.run(), other_work()), timeout=30)
    assert loop_ran.is_set()
    assert [m["id"] for m in stdout.messages()] == [1]


async def test_requests_over_the_pending_limit_are_refused_but_a_cancel_still_works() -> None:
    never = asyncio.Event()
    stdout = Output()

    async def handler(_: httpx.Request) -> httpx.Response:
        await never.wait()
        return httpx.Response(200)

    stdin = lines(
        modern("tools/call", 1, {"name": "snapshot"}),
        modern("tools/call", 2, {"name": "snapshot"}),  # over the limit of 1
        request("notifications/cancelled", None, {"requestId": 1}),
    )
    proxy = StdioProxy(
        "http://daemon/mcp", stdin=stdin, stdout=stdout, http=client_for(handler), max_pending=1
    )
    await asyncio.wait_for(proxy.run(), timeout=30)
    [refusal] = stdout.messages()
    assert refusal["id"] == 2
    assert "too many pending" in refusal["error"]["message"]


async def test_error_messages_never_carry_userinfo_or_a_query() -> None:
    stdout = Output()
    secret = "s3cret-token"
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    url = f"http://user:{secret}@127.0.0.1:{port}/mcp?token={secret}#frag"
    proxy = StdioProxy(url, stdin=lines(modern("tools/list", 9, {})), stdout=stdout)
    await asyncio.wait_for(proxy.run(), timeout=60)
    [answer] = stdout.messages()
    assert secret not in json.dumps(answer)
    assert f"http://127.0.0.1:{port}/mcp" in answer["error"]["message"]
