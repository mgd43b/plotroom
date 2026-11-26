"""Budget registration and once-only effects for cumulative usage observations."""

import math
import re
import uuid
from collections.abc import Mapping
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from fractions import Fraction
from typing import TYPE_CHECKING, Any, Literal

from plotroom.core.events import FieldSpec, LedgerError, NewEvent
from plotroom.core.next_items import ItemKind
from plotroom.core.run_reports import validate_identity
from plotroom.core.trigger_actions import SEND
from plotroom.ports.usage_meter import UsageObservation

if TYPE_CHECKING:
    from plotroom.core.unit_of_work import UnitOfWork

type KeyScope = Literal["staging", "production"]
OPENROUTER_PROVIDER = "openrouter"
_PROVIDER = re.compile(r"[a-z][a-z0-9_-]{0,63}")
BUDGET_ARGS = (
    FieldSpec("run", "str"),
    FieldSpec("lane", "str"),
    FieldSpec("cap_usd", "number"),
    FieldSpec("provider", "str"),
    FieldSpec("key_scope", "str"),
)
BUDGET_THRESHOLD = "core.budget_threshold"
BUDGET_THRESHOLD_FIELDS = (
    FieldSpec("budget_id", "str"),
    FieldSpec("run", "str"),
    FieldSpec("lane", "str"),
    FieldSpec("percent", "int"),
    FieldSpec("cap_usd", "number"),
    FieldSpec("spent_usd", "number"),
    FieldSpec("as_of", "str"),
)
BUDGET_ITEM = ItemKind("budget", 10, False, ("Review the run's spend",))


@dataclass(frozen=True)
class Budget:
    id: str
    run: str
    lane: str
    cap_usd: float
    provider: str
    key_scope: KeyScope
    spent_usd: float = 0.0
    as_of: str | None = None

    def render(self) -> dict[str, Any]:
        return asdict(self)


async def budget_open(
    uow: UnitOfWork, args: dict[str, Any], *, openrouter_bindings: Mapping[str, str] | None = None
) -> dict[str, Any]:
    run: str = args["run"]
    lane: str = args["lane"]
    provider: str = args["provider"]
    key_scope: str = args["key_scope"]
    validate_identity(run, "run")
    validate_identity(lane, "lane")
    if _PROVIDER.fullmatch(provider) is None:
        raise LedgerError("invalid", "provider must be a named meter")
    binding = None
    if provider == OPENROUTER_PROVIDER:
        binding = openrouter_bindings.get(run) if openrouter_bindings is not None else None
        if binding is None:
            raise LedgerError("invalid", "the run needs a configured OpenRouter key binding")
    if key_scope != "staging" and key_scope != "production":
        raise LedgerError("invalid", "key_scope must be staging or production")
    try:
        cap = float(args["cap_usd"])
    except OverflowError:
        cap = math.inf
    if not math.isfinite(cap) or cap <= 0:
        raise LedgerError("invalid", "cap_usd must be positive and finite")
    current = await uow.find_lane(lane)
    if current is None or current.state == "archived":
        raise LedgerError("conflict", "a budget requires a current lane")
    if await uow.find_budget(run) is not None:
        raise LedgerError("conflict", "this run already has a budget")
    if binding is not None and not await uow.reserve_openrouter_key(binding, run):
        raise LedgerError("conflict", "the OpenRouter key is already reserved for a budget")
    budget = Budget(f"bud_{uuid.uuid7()}", run, lane, cap, provider, key_scope)
    await uow.insert_budget(budget)
    return budget.render()


async def record_budget_observations(
    uow: UnitOfWork, readings: tuple[UsageObservation, ...]
) -> None:
    latest: dict[tuple[str, str], UsageObservation] = {}
    for reading in readings:
        if not reading.complete:
            continue
        pair = (reading.meter, reading.run)
        previous = latest.get(pair)
        if previous is None or reading.as_of > previous.as_of:
            latest[pair] = reading
    for reading in latest.values():
        budget = await uow.find_budget(reading.run)
        if budget is None or budget.provider != reading.meter:
            continue
        if budget.as_of is not None and reading.as_of <= datetime.fromisoformat(budget.as_of):
            continue
        # The current-key endpoint has no provider watermark. A repeated or lower counter
        # cannot establish newer spend for this fixed, non-resetting key.
        if (
            budget.provider == OPENROUTER_PROVIDER
            and budget.as_of is not None
            and reading.spent_usd <= budget.spent_usd
        ):
            continue
        as_of = reading.as_of.isoformat(timespec="microseconds").replace("+00:00", "Z")
        budget = replace(budget, spent_usd=reading.spent_usd, as_of=as_of)
        await uow.update_budget_spend(budget)
        await _apply_thresholds(uow, budget)


def reaches_budget_ratio(budget: Budget, threshold: int | float | Fraction) -> bool:
    if budget.as_of is None:
        return False
    # Use decimal spellings and exact products at the threshold boundary.
    ratio = Fraction(str(threshold)) if isinstance(threshold, float) else threshold
    return Fraction(str(budget.spent_usd)) >= Fraction(str(budget.cap_usd)) * ratio


async def _apply_thresholds(uow: UnitOfWork, budget: Budget) -> None:
    for percent in (50, 80, 100):
        if not reaches_budget_ratio(budget, Fraction(percent, 100)):
            break
        subject = f"{budget.id}:{percent}"
        fields = {
            "budget_id": budget.id,
            "run": budget.run,
            "lane": budget.lane,
            "percent": percent,
            "cap_usd": budget.cap_usd,
            "spent_usd": budget.spent_usd,
            "as_of": budget.as_of,
        }
        # Keep the lane in the fields so meter activity does not reset the lane's idle clock.
        threshold = await uow.append(NewEvent("core", BUDGET_THRESHOLD, subject, fields))
        if not threshold.created:
            continue
        item = await uow.raise_item(BUDGET_ITEM.name, subject, "orchestrator", fields)
        if percent == 100:
            lane = await uow.find_lane(budget.lane)
            if lane is not None and lane.state != "archived":
                await uow.enqueue_action(
                    threshold.event,
                    SEND,
                    {"worker": lane.worker, "lane": lane.id, "text": "stop"},
                    stable_key=item.dedup_key,
                )
