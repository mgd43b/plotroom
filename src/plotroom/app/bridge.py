"""Connect one explicit local Switchboard board to a scoped MCP server."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
from collections.abc import Sequence
from time import monotonic
from typing import Any, Literal, cast
from uuid import uuid4

import httpx2
from mcp import ClientSession
from mcp.client.client import Client
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.streamable_http import streamable_http_client
from mcp.types import TextContent

from plotroom.adapters.mcp.proxy import EndpointError, check_endpoint
from plotroom.adapters.switchboard import NAME, WAIT_SECONDS, call_relay

log = logging.getLogger("plotroom.bridge")


async def _tool(client: Client, name: str, **arguments: Any) -> dict[str, Any]:
    result = await client.call_tool(name, arguments)
    if result.is_error or not result.content or not isinstance(result.content[0], TextContent):
        raise ValueError("the server did not accept a bridge operation")
    decoded: Any = json.loads(result.content[0].text)
    envelope = cast("dict[str, Any]", decoded) if isinstance(decoded, dict) else {}
    if envelope.get("ok") is not True:
        raise ValueError("the server rejected a bridge operation")
    data = envelope.get("data")
    if not isinstance(data, dict):
        raise ValueError("the server did not return bridge data")
    return cast("dict[str, Any]", data)


async def _receive(relay: ClientSession, client: Client, board: str) -> None:
    while True:
        batch = await call_relay(relay, "wait", {"timeout_s": WAIT_SECONDS}, WAIT_SECONDS + 5)
        messages = batch.get("messages")
        if not isinstance(messages, list):
            raise ValueError("the relay did not return a message list")
        for raw in cast("list[Any]", messages):
            if not isinstance(raw, dict):
                raise ValueError("the relay returned an invalid claim")
            message = cast("dict[str, Any]", raw)
            message_id, sender, body, receipt = (
                message.get(key) for key in ("id", "from", "body", "receipt")
            )
            if (
                type(message_id) is not int
                or not isinstance(sender, str)
                or not isinstance(body, str)
                or not isinstance(receipt, str)
                or not receipt
            ):
                raise ValueError("the relay returned an invalid claim")
            await _tool(
                client,
                "bridge_receive",
                board=board,
                message_id=message_id,
                sender=sender,
                body=body,
            )
            ack = await call_relay(relay, "ack", {"id": message_id, "receipt": receipt})
            if ack.get("ok") is not True:
                log.warning("bridge receipt expired before acknowledgment")


async def _send(relay: ClientSession, client: Client, consumer_id: str, board: str) -> None:
    while True:
        wake = await _tool(client, "wait", timeout=WAIT_SECONDS)
        item = wake["item"]
        if item is None or item["kind"] != "bridge_ready":
            continue
        requested_at = monotonic()
        result = await _tool(
            client, "bridge_lease", request_id=str(uuid4()), consumer_id=consumer_id, board=board
        )
        lease = result["lease"]
        if lease is None:
            continue
        lease_deadline = requested_at + lease["lease_seconds"]
        if monotonic() + 10 >= lease_deadline:
            continue
        key, address, body = (lease[name] for name in ("action_key", "to", "body"))
        sent = await call_relay(
            relay,
            "send",
            {
                "to": address,
                "body": json.dumps({"action_key": key, "body": body}, ensure_ascii=False),
                "idempotency_key": key,
            },
        )
        if type(sent.get("id")) is not int or sent.get("to") != address:
            raise ValueError("the relay did not accept a bridge send")
        await _tool(client, "bridge_done", act_id=lease["act_id"])


async def _bridge(
    url: str, board: str, token: str, mode: Literal["auto", "legacy"], command: Sequence[str]
) -> None:
    params = StdioServerParameters(
        command=command[0],
        args=list(command[1:]),
        env={"SWITCHBOARD_BOARD": board, "SWITCHBOARD_AUTO": "0"},
    )
    consumer = str(uuid4())
    delay = 0.25
    while True:
        ready_at: float | None = None
        try:
            with open(os.devnull, "w", encoding="utf-8") as errlog:  # noqa: ASYNC230
                async with (
                    stdio_client(params, errlog=errlog) as (read, write),
                    ClientSession(read, write, read_timeout_seconds=10) as relay,
                    httpx2.AsyncClient(
                        headers={"Authorization": f"Bearer {token}"},
                        trust_env=False,
                        timeout=httpx2.Timeout(10, read=WAIT_SECONDS + 10),
                    ) as http,
                    Client(streamable_http_client(url, http_client=http), mode=mode) as client,
                ):
                    await relay.initialize()
                    registration = await call_relay(relay, "register", {"name": NAME})
                    if registration.get("ok") is not True or registration.get("you") != NAME:
                        raise ValueError("the relay refused the protected bridge address")
                    ready_at = monotonic()
                    async with asyncio.TaskGroup() as directions:
                        directions.create_task(_receive(relay, client, board))
                        directions.create_task(_send(relay, client, consumer, board))
        except Exception:
            if ready_at is not None and monotonic() - ready_at >= WAIT_SECONDS:
                delay = 0.25
            log.warning("bridge connection unavailable, retrying", extra={"retry_seconds": delay})
        await asyncio.sleep(delay)
        delay = min(delay * 2, 30)


def run_bridge(
    url: str, board: str, mode: Literal["auto", "legacy"], command: Sequence[str]
) -> int:
    token = os.environ.get("PLOTROOM_TOKEN")
    try:
        check_endpoint(url, credentialed=True)
        if not command:
            raise EndpointError("a relay command may not be empty")
        if not token or not board:
            raise EndpointError("a board and PLOTROOM_TOKEN are required")
    except EndpointError as exc:
        log.error("invalid bridge configuration", extra={"reason": str(exc)})
        return 2
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(_bridge(url, board, token, mode, command))
    return 0
