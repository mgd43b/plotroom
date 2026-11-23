"""A local, protected participant on a configured Switchboard relay."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import Mapping
from typing import Any, cast

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.types import TextContent

from plotroom.core.actions import Action
from plotroom.core.events import NewEvent
from plotroom.core.intake import append_inbound
from plotroom.core.lanes import infer_lane
from plotroom.core.ledger import Ledger
from plotroom.core.unit_of_work import UnitOfWork

log = logging.getLogger("plotroom.switchboard")
NAME = "plotroom"
WAIT_SECONDS = 15


async def _call(
    session: ClientSession, name: str, arguments: dict[str, Any], read_seconds: float = 5
) -> dict[str, Any]:
    result = await session.call_tool(name, arguments, read_timeout_seconds=read_seconds)
    if result.is_error:
        raise ValueError(f"Switchboard {name} failed")
    payload: Any = result.structured_content
    if payload is None and result.content and isinstance(result.content[0], TextContent):
        payload = json.loads(result.content[0].text)
    if not isinstance(payload, dict):
        raise ValueError(f"Switchboard {name} did not return an object")
    return cast("dict[str, Any]", payload)


class Switchboard:
    def __init__(
        self, ledger: Ledger, board: str, command: tuple[str, ...], senders: Mapping[str, str]
    ) -> None:
        self._ledger = ledger
        self._board = board
        self._server = StdioServerParameters(
            command=command[0],
            args=list(command[1:]),
            env={"SWITCHBOARD_BOARD": board, "SWITCHBOARD_AUTO": "0"},
        )
        self._senders = senders
        self._ready = asyncio.Event()
        self._session: ClientSession | None = None

    async def run(self) -> None:
        delay = 0.25
        while True:
            try:
                # The child controls its diagnostic text. Keep message data out of daemon logs.
                with open(os.devnull, "w", encoding="utf-8") as errlog:  # noqa: ASYNC230
                    async with (
                        stdio_client(self._server, errlog=errlog) as (read, write),
                        ClientSession(read, write, read_timeout_seconds=10) as session,
                    ):
                        await session.initialize()
                        registered = await _call(session, "register", {"name": NAME})
                        if registered.get("ok") is not True or registered.get("you") != NAME:
                            raise ValueError("Switchboard refused the protected address")
                        self._session = session
                        self._ready.set()
                        try:
                            while True:
                                await self.receive(session)
                                delay = 0.25
                        finally:
                            self._ready.clear()
                            self._session = None
            except Exception:
                log.warning(
                    "Switchboard receive unavailable, retrying",
                    extra={"project": self._ledger.project_id, "retry_seconds": delay},
                )
            await asyncio.sleep(delay)
            delay = min(delay * 2, 30)

    async def send(self, action: Action) -> None:
        address, body = action.payload.get("to"), action.payload.get("body")
        if not isinstance(address, str) or not address.strip() or not isinstance(body, str):
            raise ValueError("Switchboard sends require a recipient and a message body")
        address = address.strip()
        while (session := self._session) is None:
            await self._ready.wait()
        response = await _call(
            session,
            "send",
            {
                "to": address,
                "body": json.dumps({"action_key": action.key, "body": body}, ensure_ascii=False),
                "idempotency_key": action.key,
            },
        )
        if type(response.get("id")) is not int or response.get("to") != address:
            raise ValueError("Switchboard did not accept the send")

    async def receive(self, session: ClientSession) -> None:
        batch = await _call(session, "wait", {"timeout_s": WAIT_SECONDS}, WAIT_SECONDS + 5)
        messages = batch.get("messages")
        if not isinstance(messages, list):
            raise ValueError("Switchboard wait did not return a message list")
        for message in cast("list[Any]", messages):
            if not isinstance(message, dict):
                raise ValueError("Switchboard returned a malformed message")
            await self._receive_one(session, cast("dict[str, Any]", message))

    async def _receive_one(self, session: ClientSession, message: dict[str, Any]) -> None:
        message_id, sender, body, receipt = (
            message.get(k) for k in ("id", "from", "body", "receipt")
        )
        if (
            type(message_id) is not int
            or message_id < 1
            or not isinstance(sender, str)
            or not sender
            or not isinstance(body, str)
            or not isinstance(receipt, str)
            or not receipt
        ):
            raise ValueError("Switchboard returned an invalid claim")

        async def record(uow: UnitOfWork) -> None:
            lane = await infer_lane(uow, worker=self._senders.get(sender))
            await append_inbound(
                uow,
                NewEvent(
                    "switchboard",
                    "switchboard.message",
                    f"{self._board}:{message_id}",
                    {"sender": sender},
                    body,
                    lane,
                ),
            )

        await self._ledger.run("system", record)
        acknowledged = await _call(session, "ack", {"id": message_id, "receipt": receipt})
        if acknowledged.get("ok") is not True:
            log.warning("Switchboard claim expired", extra={"project": self._ledger.project_id})
