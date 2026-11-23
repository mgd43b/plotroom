"""Validated predicates and durable, one-time trigger evaluation."""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, Literal, cast

from plotroom.core.events import FieldSpec, LedgerError, NewEvent, validate_fields
from plotroom.core.trigger_actions import validate_action

if TYPE_CHECKING:
    from plotroom.core.trigger_actions import DeliveryRoute
    from plotroom.core.unit_of_work import UnitOfWork

TRIGGER_FIRED = "core.trigger_fired"
TRIGGER_FIRED_FIELDS = (FieldSpec("trigger_id", "str"),)
_MAX_DEPTH = 16

type TriggerState = Literal["open", "fired", "canceled"]


@dataclass(frozen=True)
class Trigger:
    id: str
    lane: str | None
    repository: str | None
    predicate: dict[str, Any]
    action_kind: str
    action_payload: dict[str, Any]
    state: TriggerState
    bindings: dict[str, int]
    created_seq: int
    fired_seq: int | None = None
    action_key: str | None = None

    def render(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "lane": self.lane,
            "repository": self.repository,
            "predicate": self.predicate,
            "action": {self.action_kind: self.action_payload},
            "state": self.state,
            "bindings": self.bindings,
            "created_seq": self.created_seq,
            "fired_seq": self.fired_seq,
            "action_key": self.action_key,
        }


@dataclass(frozen=True)
class Evaluation:
    uow: UnitOfWork
    trigger: Trigger
    bindings: dict[str, int]


@dataclass(frozen=True)
class LeafSpec:
    name: str
    args: tuple[FieldSpec, ...]
    mode: Literal["level", "event"]
    validate: Callable[[dict[str, Any]], None]
    evaluate: Callable[[Evaluation, str, dict[str, Any]], Awaitable[bool]]
    requires_repository: bool = False


class LeafRegistry:
    """Closed at input time: each leaf must have a registered schema and evaluator."""

    def __init__(self) -> None:
        self._leaves: dict[str, LeafSpec] = {}

    def register(self, spec: LeafSpec) -> None:
        if spec.name in self._leaves or spec.name in {"all_of", "any_of"}:
            raise ValueError(f"predicate name {spec.name!r} is already registered")
        self._leaves[spec.name] = spec

    def leaf(self, name: str) -> LeafSpec:
        spec = self._leaves.get(name)
        if spec is None:
            raise LedgerError("invalid", f"unknown predicate leaf {name!r}")
        return spec

    def validate(self, predicate: Any, *, repository: str | None, depth: int = 0) -> None:
        if depth > _MAX_DEPTH:
            raise LedgerError("invalid", f"predicate nesting exceeds {_MAX_DEPTH} levels")
        if not isinstance(predicate, dict) or len(cast("dict[str, Any]", predicate)) != 1:
            raise LedgerError("invalid", "predicate must be an object naming one leaf or group")
        name, value = next(iter(cast("dict[str, Any]", predicate).items()))
        if name in {"all_of", "any_of"}:
            if not isinstance(value, list) or not value:
                raise LedgerError("invalid", f"{name} requires a nonempty array of predicates")
            for child in cast("list[Any]", value):
                self.validate(child, repository=repository, depth=depth + 1)
            return
        leaf = self.leaf(name)
        if not isinstance(value, dict):
            raise LedgerError("invalid", f"{name} arguments must be an object")
        validate_fields(leaf.args, cast("dict[str, Any]", value), name)
        leaf.validate(cast("dict[str, Any]", value))
        if leaf.requires_repository and repository is None:
            raise LedgerError("invalid", f"{name} requires a configured GitHub repository")

    async def _holds(
        self, evaluation: Evaluation, predicate: dict[str, Any], path: str = "$"
    ) -> bool:
        name, value = next(iter(predicate.items()))
        if name not in {"all_of", "any_of"}:
            return await self.leaf(name).evaluate(evaluation, path, value)
        # Evaluate every branch so glob bindings survive independently of the group's result.
        matches = [
            await self._holds(evaluation, child, f"{path}/{index}")
            for index, child in enumerate(cast("list[dict[str, Any]]", value))
        ]
        return all(matches) if name == "all_of" else any(matches)

    async def evaluate_open(self, uow: UnitOfWork) -> None:
        for trigger in await uow.open_triggers():
            bindings = trigger.bindings.copy()
            if await self._holds(Evaluation(uow, trigger, bindings), trigger.predicate):
                fired = await uow.append(
                    NewEvent(
                        "core",
                        TRIGGER_FIRED,
                        trigger.id,
                        {"trigger_id": trigger.id},
                        lane_id=trigger.lane,
                    )
                )
                key = await uow.enqueue_action(
                    fired.event, trigger.action_kind, trigger.action_payload
                )
                await uow.save_trigger(
                    replace(
                        trigger,
                        state="fired",
                        fired_seq=fired.event.seq,
                        action_key=key,
                        bindings=bindings,
                    )
                )
            elif bindings != trigger.bindings:
                await uow.save_trigger(replace(trigger, bindings=bindings))


def _validate_pr_selector(args: dict[str, Any]) -> None:
    if len(args) != 1:
        raise LedgerError("invalid", "pr_merged requires exactly one number or branch_glob")
    if "number" in args and not 0 < args["number"] <= 2**63 - 1:
        raise LedgerError("invalid", "pr_merged number must be a positive SQLite integer")
    if "branch_glob" in args and (not args["branch_glob"].strip() or "\0" in args["branch_glob"]):
        raise LedgerError("invalid", "pr_merged branch_glob must be a nonempty branch pattern")


async def _pr_merged(evaluation: Evaluation, path: str, args: dict[str, Any]) -> bool:
    repository = evaluation.trigger.repository
    if repository is None:
        return False
    number: int | None = args.get("number", evaluation.bindings.get(path))
    if number is None:
        number = await evaluation.uow.first_matching_pull(repository, args["branch_glob"])
        if number is None:
            return False
        evaluation.bindings[path] = number
    pull = await evaluation.uow.pull_request(repository, number)
    return pull is not None and pull.complete and pull.state == "merged"


def build_leaf_registry() -> LeafRegistry:
    registry = LeafRegistry()
    registry.register(
        LeafSpec(
            "pr_merged",
            (FieldSpec("number", "int", required=False), FieldSpec("branch_glob", "str", False)),
            "level",
            _validate_pr_selector,
            _pr_merged,
            requires_repository=True,
        )
    )
    return registry


async def create_trigger(
    uow: UnitOfWork,
    args: dict[str, Any],
    *,
    registry: LeafRegistry,
    repository: str | None,
    delivery_routes: Mapping[str, DeliveryRoute],
) -> dict[str, Any]:
    predicate: dict[str, Any] = args["predicate"]
    registry.validate(predicate, repository=repository)
    kind, payload, default_lane = await validate_action(uow, args["action"], delivery_routes)
    lane: str | None = args.get("lane", default_lane)
    if lane is not None:
        try:
            lane_size = len(lane.encode("utf-8"))
        except UnicodeError:
            raise LedgerError("invalid", "lane must be valid UTF-8") from None
        if not lane.strip() or lane_size > 128:
            raise LedgerError("invalid", "lane must be 1-128 UTF-8 bytes")
    trigger = Trigger(
        f"trigger_{uuid.uuid7()}",
        lane,
        repository,
        predicate,
        kind,
        payload,
        "open",
        {},
        await uow.allocate_seq(),
    )
    await uow.save_trigger(trigger)
    return {"id": trigger.id}


async def cancel_trigger(uow: UnitOfWork, args: dict[str, Any]) -> dict[str, Any]:
    trigger = await uow.find_trigger(args["trigger_id"])
    if trigger is None:
        raise LedgerError("not_found", "trigger not found")
    if trigger.state == "fired":
        raise LedgerError("conflict", "trigger has already fired")
    if trigger.state != "canceled":
        await uow.save_trigger(replace(trigger, state="canceled"))
    return {"id": trigger.id, "state": "canceled"}
