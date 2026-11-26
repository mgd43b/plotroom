"""Read cumulative USD spend from each run's dedicated OpenRouter inference key."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from hmac import compare_digest
from pathlib import Path
from typing import Any, cast

import httpx

from plotroom._secret_file import read_secret_file
from plotroom.core.budgets import OPENROUTER_PROVIDER
from plotroom.ports.clock import Clock
from plotroom.ports.usage_meter import UsageObservation

log = logging.getLogger("plotroom.openrouter")
_KEY_URL = "https://openrouter.ai/api/v1/key"
_KEY = re.compile(r"sk-or-v1-[a-zA-Z0-9-]+")


@dataclass(frozen=True)
class OpenRouterRun:
    key_file: Path = field(repr=False)
    key_sha256: str = field(repr=False)

    @property
    def reservation(self) -> str:
        # Preserve the binding across restart without storing the mounted key or its pin.
        return hashlib.sha256(
            b"plotroom.openrouter.reservation:" + bytes.fromhex(self.key_sha256)
        ).hexdigest()


def _unique_fields(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    fields = dict(pairs)
    if len(fields) != len(pairs):
        raise ValueError("duplicate current-key fields")
    return fields


def _money(value: object) -> Decimal:
    if type(value) not in (int, Decimal):
        raise ValueError("expected a numeric USD amount")
    amount = Decimal(cast("int | Decimal", value))
    if not amount.is_finite() or amount < 0:
        raise ValueError("expected a finite nonnegative USD amount")
    return amount


def _usage(response: httpx.Response, cap_usd: float) -> Decimal:
    body: Any = json.loads(response.content, parse_float=Decimal, object_pairs_hook=_unique_fields)
    if not isinstance(body, dict):
        raise ValueError("expected a current-key response")
    data: Any = cast("dict[str, Any]", body).get("data")
    if not isinstance(data, dict):
        raise ValueError("expected current-key data")
    fields = cast("dict[str, Any]", data)
    limit = _money(fields.get("limit"))
    if (
        not 0 < limit <= Decimal(str(cap_usd))
        or "limit_reset" not in fields
        or fields["limit_reset"] is not None
        or fields.get("is_management_key") is not False
        or fields.get("is_provisioning_key") is not False
        or _money(fields.get("byok_usage")) != 0
        or _money(fields.get("limit_remaining")) > limit
    ):
        raise ValueError("key does not match the capped run contract")
    return _money(fields.get("usage"))


class OpenRouterUsageMeter:
    def __init__(
        self,
        runs: Mapping[str, OpenRouterRun],
        budgets: Callable[[], Awaitable[list[tuple[str, float, str]]]],
        clock: Clock,
        *,
        timeout: float = 5,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._runs = dict(runs)
        self._budgets = budgets
        self._clock = clock
        self._timeout = timeout
        self._transport = transport

    async def observations(self) -> tuple[UsageObservation, ...]:
        targets = await self._budgets()
        if not targets:
            return ()
        async with httpx.AsyncClient(
            transport=self._transport,
            timeout=self._timeout,
            follow_redirects=False,
            trust_env=False,
        ) as client:
            readings = await asyncio.gather(
                *(self._observe(client, run, cap, reservation) for run, cap, reservation in targets)
            )
        return tuple(reading for reading in readings if reading is not None)

    async def _observe(
        self, client: httpx.AsyncClient, run: str, cap_usd: float, reservation: str
    ) -> UsageObservation | None:
        binding = self._runs.get(run)
        if binding is None or not compare_digest(binding.reservation, reservation):
            log.warning("OpenRouter run binding does not match its recorded reservation")
            return None
        try:
            async with asyncio.timeout(self._timeout):
                key = await read_secret_file(binding.key_file)
                if _KEY.fullmatch(key) is None or not compare_digest(
                    hashlib.sha256(key.encode("ascii")).hexdigest(), binding.key_sha256
                ):
                    raise ValueError("mounted key differs from its run binding")
                response = await client.get(_KEY_URL, headers={"Authorization": f"Bearer {key}"})
                response.raise_for_status()
                amount = _usage(response, cap_usd)
                spend = float(amount)
                if spend == 0 and amount:
                    raise ValueError("USD amount is too small for the budget")
                return UsageObservation(OPENROUTER_PROVIDER, run, spend, self._clock.now())
        except (
            OSError,
            ValueError,
            OverflowError,
            InvalidOperation,
            RecursionError,
            httpx.HTTPError,
        ):
            log.warning("OpenRouter usage observation unavailable")
            return None
