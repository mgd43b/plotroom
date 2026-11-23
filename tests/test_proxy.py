"""The stdio proxy against a scripted HTTP peer: headers, SSE, concurrency, cancel, failures."""

from __future__ import annotations

import asyncio
import io
import json
import threading
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from mcp_types import PROTOCOL_VERSION_META_KEY

from plotroom.adapters.mcp.proxy import (
    EndpointError,
    StdioProxy,
    UpstreamStartError,
    check_endpoint,
    envelope_headers,
)

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


async def test_an_unreachable_server_answers_the_request_with_an_error(held_port: int) -> None:
    stdout = Output()
    proxy = StdioProxy(
        f"http://127.0.0.1:{held_port}/mcp", stdin=lines(modern("tools/list", 9, {})), stdout=stdout
    )
    await asyncio.wait_for(proxy.run(), timeout=60)
    [answer] = stdout.messages()
    assert answer["id"] == 9
    assert answer["error"]["code"] == -32603
    assert "plotroom serve" in answer["error"]["message"]


async def test_an_unreachable_server_is_silent_for_notifications(held_port: int) -> None:
    stdout = Output()
    proxy = StdioProxy(
        f"http://127.0.0.1:{held_port}/mcp",
        stdin=lines(request("notifications/initialized", None)),
        stdout=stdout,
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


async def test_error_messages_never_carry_userinfo_or_a_query(held_port: int) -> None:
    stdout = Output()
    secret = "s3cret-token"
    port = held_port
    url = f"http://user:{secret}@127.0.0.1:{port}/mcp?token={secret}#frag"
    proxy = StdioProxy(url, stdin=lines(modern("tools/list", 9, {})), stdout=stdout)
    await asyncio.wait_for(proxy.run(), timeout=60)
    [answer] = stdout.messages()
    assert secret not in json.dumps(answer)
    assert f"http://127.0.0.1:{port}/mcp" in answer["error"]["message"]


async def test_a_method_that_cannot_be_an_http_header_is_answered_with_an_error() -> None:
    stdout = Output()
    message = modern("tôols/list", 14, {})
    proxy = StdioProxy(
        "http://daemon/mcp", stdin=lines(message), stdout=stdout, http=client_for(accepted)
    )
    await asyncio.wait_for(proxy.run(), timeout=30)
    [answer] = stdout.messages()
    assert answer["id"] == 14
    assert "cannot be forwarded" in answer["error"]["message"]


async def test_a_boolean_cancel_target_does_not_cancel_request_one() -> None:
    gate = asyncio.Event()
    stdout = Output()

    async def handler(http_request: httpx.Request) -> httpx.Response:
        await gate.wait()
        return httpx.Response(
            200,
            content=result_for(json.loads(http_request.content)),
            headers={"content-type": "application/json"},
        )

    class Stdin:
        def __init__(self) -> None:
            self._script = [
                modern("tools/list", 1, {}),
                request("notifications/cancelled", None, {"requestId": True}),
            ]

        def readline(self) -> bytes:
            if not self._script:
                asyncio.run_coroutine_threadsafe(_set(gate), loop).result(timeout=30)
                return b""
            return json.dumps(self._script.pop(0)).encode() + b"\n"

    async def _set(event: asyncio.Event) -> None:
        event.set()

    loop = asyncio.get_running_loop()
    proxy = StdioProxy("http://daemon/mcp", stdin=Stdin(), stdout=stdout, http=client_for(handler))  # pyright: ignore[reportArgumentType]
    await asyncio.wait_for(proxy.run(), timeout=30)
    assert [m["id"] for m in stdout.messages()] == [1]


async def test_a_stream_cut_after_its_response_does_not_add_a_second_answer() -> None:
    stdout = Output()
    final: dict[str, Any] = {"jsonrpc": "2.0", "id": 3, "result": {}}

    class CutStream(httpx.AsyncByteStream):
        async def __aiter__(self) -> AsyncIterator[bytes]:
            yield f"data: {json.dumps(final)}\n\n".encode()
            raise httpx.ReadError("connection lost")

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, stream=CutStream(), headers={"content-type": "text/event-stream"}
        )

    proxy = StdioProxy(
        "http://daemon/mcp",
        stdin=lines(modern("tools/call", 3, {"name": "snapshot"})),
        stdout=stdout,
        http=client_for(handler),
    )
    await asyncio.wait_for(proxy.run(), timeout=30)
    assert stdout.messages() == [final]


async def test_a_write_left_behind_by_a_cancelled_request_still_counts_as_pending() -> None:
    write_started = threading.Event()
    release = threading.Event()

    class StuckOutput(Output):
        def write(self, data: Any) -> int:
            write_started.set()
            assert release.wait(timeout=30)
            return super().write(data)

    stdout = StuckOutput()

    def handler(http_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=result_for(json.loads(http_request.content)),
            headers={"content-type": "application/json"},
        )

    class Stdin:
        def __init__(self) -> None:
            self._script = [
                modern("tools/list", 1, {}),
                request("notifications/cancelled", None, {"requestId": 1}),
                modern("tools/list", 2, {}),
            ]

        def readline(self) -> bytes:
            if len(self._script) == 2:  # request 1 is answered and its write is stuck
                assert write_started.wait(timeout=30)
            if not self._script:
                release.set()
                return b""
            return json.dumps(self._script.pop(0)).encode() + b"\n"

    proxy = StdioProxy(
        "http://daemon/mcp",
        stdin=Stdin(),  # pyright: ignore[reportArgumentType]
        stdout=stdout,
        http=client_for(handler),
        max_pending=1,
    )
    await asyncio.wait_for(proxy.run(), timeout=30)
    ids = [m["id"] for m in stdout.messages()]
    assert ids.count(2) == 1
    refusal = next(m for m in stdout.messages() if m["id"] == 2)
    assert "too many pending" in refusal["error"]["message"]


@pytest.mark.parametrize(
    "url",
    [
        "http://user:pw@plotroom.example.org/mcp",
        "http://plotroom.example.org/mcp?token=abc",
        "ftp://127.0.0.1/mcp",
        "http:///mcp",
        "http://127.0.0.1:notaport/mcp",
    ],
)
def test_unsafe_or_unusable_endpoints_are_refused(url: str) -> None:
    with pytest.raises(EndpointError):
        check_endpoint(url)


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8765/mcp",
        "http://localhost:8765/mcp",
        "http://[::1]:8765/mcp",
        "http://user:pw@127.0.0.1:8765/mcp?token=abc",
        "https://user:pw@plotroom.example.org/mcp?token=abc",
        "http://plotroom.example.org/mcp",
    ],
)
def test_loopback_https_and_unauthenticated_endpoints_are_allowed(url: str) -> None:
    check_endpoint(url)


async def test_refusals_stay_bounded_while_a_cancelled_write_is_stuck() -> None:
    write_started = threading.Event()
    release = threading.Event()

    class StuckOutput(Output):
        def write(self, data: Any) -> int:
            write_started.set()
            assert release.wait(timeout=30)
            return super().write(data)

    stdout = StuckOutput()

    def handler(http_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=result_for(json.loads(http_request.content)),
            headers={"content-type": "application/json"},
        )

    class Stdin:
        def __init__(self) -> None:
            self._script = [
                modern("tools/list", 1, {}),
                request("notifications/cancelled", None, {"requestId": 1}),
                modern("tools/list", 2, {}),
                modern("tools/list", 3, {}),
                modern("tools/list", 4, {}),
            ]

        def readline(self) -> bytes:
            if len(self._script) == 4:  # request 1 is answered and its write is stuck
                assert write_started.wait(timeout=30)
            if not self._script:
                release.set()
                return b""
            return json.dumps(self._script.pop(0)).encode() + b"\n"

    proxy = StdioProxy(
        "http://daemon/mcp",
        stdin=Stdin(),  # pyright: ignore[reportArgumentType]
        stdout=stdout,
        http=client_for(handler),
        max_pending=1,
    )
    await asyncio.wait_for(proxy.run(), timeout=30)
    # one orphaned write plus one refusal reach the limit of 2 * max_pending; the rest are dropped
    assert [m["id"] for m in stdout.messages() if "error" in m] == [2]


# --- starting the server on demand ---------------------------------------------------------


def ok_handler(http_request: httpx.Request) -> httpx.Response:
    body = json.loads(http_request.content)
    return httpx.Response(
        200, content=result_for(body), headers={"content-type": "application/json"}
    )


async def test_requests_arriving_together_start_the_server_once_and_wait_for_it() -> None:
    release = asyncio.Event()
    both_dispatched = asyncio.Event()
    loop = asyncio.get_running_loop()
    starts: list[int] = []
    sent: list[int] = []

    async def ensure() -> None:
        starts.append(1)
        await release.wait()

    def handler(http_request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(http_request.content)["id"])
        return ok_handler(http_request)

    class Stdin:
        """Two requests, then end of input. Reading the end means both are already dispatched."""

        def __init__(self) -> None:
            self._script = [modern("tools/list", 1, {}), modern("tools/list", 2, {})]

        def readline(self) -> bytes:
            if self._script:
                return json.dumps(self._script.pop(0)).encode() + b"\n"
            loop.call_soon_threadsafe(both_dispatched.set)
            return b""

    stdout = Output()
    proxy = StdioProxy(
        "http://daemon/mcp",
        stdin=Stdin(),  # pyright: ignore[reportArgumentType]
        stdout=stdout,
        http=client_for(handler),
        ensure=ensure,
    )
    running = asyncio.ensure_future(proxy.run())
    await asyncio.wait_for(both_dispatched.wait(), timeout=30)
    for _ in range(20):  # loop turns, not time: a gate that let a request through would show by now
        await asyncio.sleep(0)
    assert sent == [], "nothing is forwarded before the server is up"
    release.set()
    await asyncio.wait_for(running, timeout=30)
    assert starts == [1]
    assert sorted(m["id"] for m in stdout.messages()) == [1, 2]


async def test_a_server_that_cannot_start_answers_each_request_with_the_reason() -> None:
    attempts: list[int] = []

    async def ensure() -> None:
        attempts.append(1)
        raise UpstreamStartError("data directory is locked: pid 42")

    stdout = Output()
    proxy = StdioProxy(
        "http://daemon/mcp",
        stdin=lines(modern("tools/list", 1, {}), request("notifications/initialized", None)),
        stdout=stdout,
        http=client_for(ok_handler),
        ensure=ensure,
    )
    await asyncio.wait_for(proxy.run(), timeout=30)
    (reply,) = stdout.messages()
    assert reply["id"] == 1
    assert "data directory is locked: pid 42" in reply["error"]["message"]
    assert attempts, "every request after a failure tries the start again"


async def test_a_refused_connection_restarts_the_server_and_retries_the_request_once() -> None:
    starts: list[int] = []
    posts: list[int] = []

    async def ensure() -> None:
        starts.append(1)

    def handler(http_request: httpx.Request) -> httpx.Response:
        posts.append(1)
        if len(posts) == 1:
            raise httpx.ConnectError("refused", request=http_request)
        return ok_handler(http_request)

    stdout = Output()
    proxy = StdioProxy(
        "http://daemon/mcp",
        stdin=lines(modern("tools/list", 1, {})),
        stdout=stdout,
        http=client_for(handler),
        ensure=ensure,
    )
    await asyncio.wait_for(proxy.run(), timeout=30)
    assert starts == [1, 1], "once at the first request, once after the refusal"
    assert posts == [1, 1]
    assert [m["id"] for m in stdout.messages()] == [1]


async def test_a_start_that_reports_where_the_server_listens_moves_the_proxy_there() -> None:
    reported = ["http://127.0.0.1:41001/mcp", "http://127.0.0.1:41002/mcp"]
    reached: list[str] = []

    async def ensure() -> str:
        return reported.pop(0)

    def handler(http_request: httpx.Request) -> httpx.Response:
        reached.append(str(http_request.url))
        if len(reached) == 1:  # the first daemon is gone by the time the request arrives
            raise httpx.ConnectError("refused", request=http_request)
        return ok_handler(http_request)

    stdout = Output()
    proxy = StdioProxy(
        "http://127.0.0.1:0/mcp",
        stdin=lines(modern("tools/list", 1, {})),
        stdout=stdout,
        http=client_for(handler),
        ensure=ensure,
    )
    await asyncio.wait_for(proxy.run(), timeout=30)
    assert reached == ["http://127.0.0.1:41001/mcp", "http://127.0.0.1:41002/mcp"]
    assert [m["id"] for m in stdout.messages()] == [1]


async def test_errors_name_the_address_a_start_reported_not_the_configured_one() -> None:
    async def ensure() -> str:
        return "http://127.0.0.1:41003/mcp"

    def handler(http_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=http_request)

    stdout = Output()
    proxy = StdioProxy(
        "http://127.0.0.1:0/mcp",
        stdin=lines(modern("tools/list", 1, {})),
        stdout=stdout,
        http=client_for(handler),
        ensure=ensure,
    )
    await asyncio.wait_for(proxy.run(), timeout=30)
    (reply,) = stdout.messages()
    assert "http://127.0.0.1:41003/mcp" in reply["error"]["message"]


async def test_a_refusal_after_the_restart_is_an_error_and_not_a_loop() -> None:
    starts: list[int] = []

    async def ensure() -> None:
        starts.append(1)

    def handler(http_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=http_request)

    stdout = Output()
    proxy = StdioProxy(
        "http://daemon/mcp",
        stdin=lines(modern("tools/list", 1, {})),
        stdout=stdout,
        http=client_for(handler),
        ensure=ensure,
    )
    await asyncio.wait_for(proxy.run(), timeout=30)
    assert starts == [1, 1]
    (reply,) = stdout.messages()
    assert "cannot reach plotroom" in reply["error"]["message"]


async def test_without_a_starter_a_refused_connection_is_not_retried() -> None:
    posts: list[int] = []

    def handler(http_request: httpx.Request) -> httpx.Response:
        posts.append(1)
        raise httpx.ConnectError("refused", request=http_request)

    stdout = Output()
    proxy = StdioProxy(
        "http://daemon/mcp",
        stdin=lines(modern("tools/list", 1, {})),
        stdout=stdout,
        http=client_for(handler),
    )
    await asyncio.wait_for(proxy.run(), timeout=30)
    assert posts == [1]
    assert len(stdout.messages()) == 1


async def test_a_notifications_empty_202_is_not_logged_as_an_error(
    caplog: pytest.LogCaptureFixture,
) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(202)

    proxy = StdioProxy(
        "http://daemon/mcp",
        stdin=lines(request("notifications/initialized", None)),
        stdout=Output(),
        http=client_for(handler),
    )
    with caplog.at_level("ERROR", logger="plotroom.proxy"):
        await asyncio.wait_for(proxy.run(), timeout=30)
    assert caplog.records == []


# --- the client going away ------------------------------------------------------------------


async def test_closing_stdin_cancels_a_listen_stream_but_lets_other_calls_finish() -> None:
    never = asyncio.Event()
    answer_listen_done = asyncio.Event()

    async def handler(http_request: httpx.Request) -> httpx.Response:
        body = json.loads(http_request.content)
        if body["method"] == "subscriptions/listen":
            answer_listen_done.set()
            await never.wait()
        else:
            await answer_listen_done.wait()
        return httpx.Response(
            200, content=result_for(body), headers={"content-type": "application/json"}
        )

    stdout = Output()
    proxy = StdioProxy(
        "http://daemon/mcp",
        stdin=lines(modern("subscriptions/listen", 1, {}), modern("tools/list", 2, {})),
        stdout=stdout,
        http=client_for(handler),
    )
    await asyncio.wait_for(proxy.run(), timeout=30)
    assert [m["id"] for m in stdout.messages()] == [2], "the call finished; the stream was ended"


async def test_a_vanished_client_stops_every_pending_call() -> None:
    never = asyncio.Event()
    started = asyncio.Event()
    canceled: list[int] = []

    async def handler(http_request: httpx.Request) -> httpx.Response:
        body = json.loads(http_request.content)
        if body["id"] == 1:
            await started.wait()  # answered only once the second call is pending
            return httpx.Response(
                200, content=result_for(body), headers={"content-type": "application/json"}
            )
        started.set()
        try:
            await never.wait()
        except asyncio.CancelledError:
            canceled.append(body["id"])
            raise
        return httpx.Response(200)

    class Gone(io.BytesIO):
        def write(self, data: Any) -> int:
            raise BrokenPipeError

    proxy = StdioProxy(
        "http://daemon/mcp",
        stdin=lines(modern("tools/list", 1, {}), modern("tools/list", 2, {})),
        stdout=Gone(),
        http=client_for(handler),
    )
    await asyncio.wait_for(proxy.run(), timeout=30)
    assert canceled == [2]


async def test_requests_refused_together_share_one_restart() -> None:
    starts: list[int] = []
    refused = 0
    all_refused = asyncio.Event()

    async def ensure() -> None:
        starts.append(1)

    async def handler(http_request: httpx.Request) -> httpx.Response:
        nonlocal refused
        if len(starts) == 1:  # the daemon is down until the restart
            refused += 1
            if refused == 3:
                all_refused.set()
            await all_refused.wait()  # each of the three is refused before any restarts
            raise httpx.ConnectError("refused", request=http_request)
        return ok_handler(http_request)

    stdout = Output()
    proxy = StdioProxy(
        "http://daemon/mcp",
        stdin=lines(*(modern("tools/list", n, {}) for n in (1, 2, 3))),
        stdout=stdout,
        http=client_for(handler),
        ensure=ensure,
    )
    await asyncio.wait_for(proxy.run(), timeout=30)
    assert starts == [1, 1], "one start at first use and one shared restart, not one per request"
    assert sorted(m["id"] for m in stdout.messages()) == [1, 2, 3]
    assert all("result" in m for m in stdout.messages())
