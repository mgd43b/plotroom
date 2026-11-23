from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from fakes import FixedClock
from plotroom.adapters.usage_fixture import FixtureUsageMeter
from plotroom.app.runtime import ProjectRuntime
from plotroom.core.events import LedgerError
from plotroom.core.ledger import Ledger
from plotroom.core.triggers import TRIGGER_FIRED
from plotroom.ports.usage_meter import UsageObservation
from test_budgets import BUDGET_ARGS, ORCHESTRATOR, open_ledger, start_lane
from test_triggers import notification


async def fired_triggers(ledger: Ledger) -> list[str]:
    return [
        event.fields["trigger_id"]
        for event in await ledger.events_after(0)
        if event.kind == TRIGGER_FIRED
    ]


async def test_budget_ratio_rises_on_latest_spend_and_survives_restart(tmp_path: Path) -> None:
    clock = FixedClock()
    start = clock.now()
    meter = FixtureUsageMeter()
    spec = notification({"budget_ratio": {"run": "run", "gte": 0.9}})

    async def poll(runtime: ProjectRuntime, spend: float, second: int) -> None:
        meter.readings = (
            UsageObservation("fixture", "run", spend, start + timedelta(seconds=second)),
        )
        clock.advance(60)
        assert await runtime.tick_if_due()

    async with open_ledger(tmp_path, clock) as ledger:
        await start_lane(ledger)
        await ledger.execute(ORCHESTRATOR, "budget_open", "open", BUDGET_ARGS)
        once = (await ledger.execute(ORCHESTRATOR, "trigger", "once", spec)).result["id"]
        every = (
            await ledger.execute(ORCHESTRATOR, "trigger", "every", {**spec, "mode": "every"})
        ).result["id"]
        runtime = ProjectRuntime(
            ledger, clock, tick_seconds=60, action_attempts=2, usage_meter=meter
        )

        await poll(runtime, 0.08, 10)
        await poll(runtime, 0.09, 9)
        assert await fired_triggers(ledger) == []
        await poll(runtime, 0.09, 20)
        assert sorted(await fired_triggers(ledger)) == sorted([once, every])
        await poll(runtime, 0.095, 30)
        assert sorted(await fired_triggers(ledger)) == sorted([once, every])

    async with open_ledger(tmp_path, clock) as ledger:
        runtime = ProjectRuntime(
            ledger, clock, tick_seconds=60, action_attempts=2, usage_meter=meter
        )
        await poll(runtime, 0.095, 30)
        await poll(runtime, 0.01, 29)
        await poll(runtime, 0.096, 40)
        assert (await ledger.execute(ORCHESTRATOR, "trigger", "once", spec)).replayed
        assert sorted(await fired_triggers(ledger)) == sorted([once, every])

        await poll(runtime, 0.01, 50)
        await poll(runtime, 0.09, 60)
        assert sorted(await fired_triggers(ledger)) == sorted([once, every, every])


async def test_budget_ratio_requires_observation_and_supports_ratios_above_one(
    tmp_path: Path,
) -> None:
    clock = FixedClock()
    meter = FixtureUsageMeter()
    async with open_ledger(tmp_path, clock) as ledger:
        await start_lane(ledger)
        await ledger.execute(ORCHESTRATOR, "budget_open", "open", BUDGET_ARGS)

        async def create(request_id: str, run: str, threshold: int | float) -> str:
            result = await ledger.execute(
                ORCHESTRATOR,
                "trigger",
                request_id,
                notification({"budget_ratio": {"run": run, "gte": threshold}}),
            )
            return result.result["id"]

        await create("unknown", "unknown", 0)
        observed = await create("unobserved", "run", 0)
        over_cap = await create("over-cap", "run", 1.1)
        assert await fired_triggers(ledger) == []

        runtime = ProjectRuntime(
            ledger, clock, tick_seconds=60, action_attempts=2, usage_meter=meter
        )
        meter.readings = (UsageObservation("fixture", "run", 0, clock.now()),)
        clock.advance(60)
        assert await runtime.tick_if_due()
        assert await fired_triggers(ledger) == [observed]

        meter.readings = (UsageObservation("fixture", "run", 0.11, clock.now()),)
        clock.advance(60)
        assert await runtime.tick_if_due()
        assert await fired_triggers(ledger) == [observed, over_cap]


@pytest.mark.parametrize(
    "args",
    [
        {"run": "run"},
        {"gte": 0.9},
        {"run": "run", "gte": 0.9, "extra": True},
        {"run": "\0", "gte": 0.9},
        {"run": "run", "gte": True},
        {"run": "run", "gte": "0.9"},
        {"run": "run", "gte": -0.1},
        {"run": "run", "gte": float("inf")},
        {"run": "run", "gte": float("nan")},
    ],
)
async def test_budget_ratio_rejects_malformed_leaves(tmp_path: Path, args: dict[str, Any]) -> None:
    async with open_ledger(tmp_path, FixedClock()) as ledger:
        with pytest.raises(LedgerError) as caught:
            await ledger.execute(
                ORCHESTRATOR, "trigger", "invalid", notification({"budget_ratio": args})
            )
        assert caught.value.code == "invalid"
        assert await ledger.list_triggers(ORCHESTRATOR, None) == []
