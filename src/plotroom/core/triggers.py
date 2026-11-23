"""Validated predicates and durable trigger evaluation."""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime
from functools import partial
from typing import TYPE_CHECKING, Any, Literal, cast

from plotroom.core.checklists import CHECKLIST_EMPTY
from plotroom.core.events import FieldSpec, LedgerError, NewEvent, validate_fields
from plotroom.core.github_projection import (
    CHECKS,
    ISSUES,
    MAIN,
    THREADS,
    checks_for_head,
    main_verdict,
    unresolved_threads,
)
from plotroom.core.reports import DEPLOY_STATUS, validate_deploy_environment, validate_report_sha
from plotroom.core.roles import role_family
from plotroom.core.run_reports import RUN_ID, validate_identity
from plotroom.core.trigger_actions import validate_action

if TYPE_CHECKING:
    from plotroom.core.github import PullRequest
    from plotroom.core.trigger_actions import DeliveryRoute
    from plotroom.core.unit_of_work import UnitOfWork

TRIGGER_FIRED = "core.trigger_fired"
TRIGGER_FIRED_FIELDS = (FieldSpec("trigger_id", "str"),)
_MAX_DEPTH = 16

type TriggerMode = Literal["once", "every"]

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
    mode: TriggerMode = "once"
    was_true: bool = False
    event_floors: dict[str, int] = field(default_factory=dict[str, int])

    def render(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "lane": self.lane,
            "repository": self.repository,
            "predicate": self.predicate,
            "action": {self.action_kind: self.action_payload},
            "state": self.state,
            "mode": self.mode,
            "bindings": self.bindings,
            "created_seq": self.created_seq,
            "fired_seq": self.fired_seq,
            "action_key": self.action_key,
        }


@dataclass
class Evaluation:
    uow: UnitOfWork
    trigger: Trigger
    bindings: dict[str, int]

    matched: dict[str, int] = field(default_factory=dict[str, int])

    async def pending_event(self, path: str, kind: str, fields: Mapping[str, str | bool]) -> bool:
        cursor = max(
            self.trigger.fired_seq or self.trigger.created_seq,
            self.trigger.event_floors.get(path, 0),
        )
        event = await self.uow.latest_event(kind, fields=fields, after=cursor)
        if event is None:
            return False
        self.matched[path] = max(self.matched.get(path, 0), event.seq)
        return True


@dataclass(frozen=True)
class PredicateMatch:
    holds: bool
    level_holds: bool
    events: frozenset[str] = frozenset()


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
    ) -> PredicateMatch:
        """Evaluate each branch and track the event leaves that satisfied it."""
        name, value = next(iter(predicate.items()))
        if name not in {"all_of", "any_of"}:
            leaf = self.leaf(name)
            holds = await leaf.evaluate(evaluation, path, value)
            if leaf.mode == "level":
                return PredicateMatch(holds, holds)
            return PredicateMatch(holds, False, frozenset({path}) if holds else frozenset())
        # Evaluate every branch so glob bindings survive independently of the group's result.
        matches = [
            await self._holds(evaluation, child, f"{path}/{index}")
            for index, child in enumerate(cast("list[dict[str, Any]]", value))
        ]
        combine = all if name == "all_of" else any
        holds = combine(match.holds for match in matches)
        events = (
            frozenset(path for match in matches for path in match.events)
            if holds
            else frozenset[str]()
        )
        return PredicateMatch(holds, combine(match.level_holds for match in matches), events)

    async def evaluate_open(self, uow: UnitOfWork) -> None:
        for trigger in await uow.open_triggers():
            bindings = trigger.bindings.copy()
            evaluation = Evaluation(uow, trigger, bindings)
            match = await self._holds(evaluation, trigger.predicate)
            updated = replace(trigger, bindings=bindings, was_true=match.holds)
            if match.holds and not trigger.was_true:
                updated = await self._fire(uow, updated)
                if trigger.mode == "every":
                    # Last fire is the shared cursor. It supersedes any per-leaf cursors.
                    updated = replace(updated, was_true=match.level_holds, event_floors={})
            elif match.holds and match.events:
                # During a true plateau, acknowledge only events in satisfied branches.
                floors = {path: evaluation.matched[path] for path in match.events}
                updated = replace(
                    updated,
                    was_true=match.level_holds,
                    event_floors={**trigger.event_floors, **floors},
                )
            if updated != trigger:
                await uow.save_trigger(updated)

    @staticmethod
    async def _fire(uow: UnitOfWork, trigger: Trigger) -> Trigger:
        uid = trigger.id
        if trigger.mode == "every":
            uid = f"{uid}:{trigger.fired_seq or trigger.created_seq}"
        fired = await uow.append(
            NewEvent("core", TRIGGER_FIRED, uid, {"trigger_id": trigger.id}, lane_id=trigger.lane)
        )
        key = await uow.enqueue_action(fired.event, trigger.action_kind, trigger.action_payload)
        return replace(
            trigger,
            state="fired" if trigger.mode == "once" else "open",
            fired_seq=fired.event.seq,
            action_key=key,
        )


_PR_ARGS = (FieldSpec("number", "int", required=False), FieldSpec("branch_glob", "str", False))
_NUMBER = FieldSpec("number", "int")
_SHA = FieldSpec("sha", "str")
_ENV = FieldSpec("environment", "str")
_WORKER = FieldSpec("worker", "str")
_LANE = FieldSpec("lane", "str")


def _validate_number(args: dict[str, Any]) -> None:
    if not 0 < args["number"] <= 2**63 - 1:
        raise LedgerError("invalid", "number must be a positive SQLite integer")


def _validate_pr_selector(args: dict[str, Any]) -> None:
    if len(args) != 1:
        raise LedgerError("invalid", "a PR selector requires exactly one number or branch_glob")
    if "number" in args:
        _validate_number(args)
    elif not args["branch_glob"].strip() or "\0" in args["branch_glob"]:
        raise LedgerError("invalid", "branch_glob must be a nonempty branch pattern")


async def _select_pr(evaluation: Evaluation, path: str, args: dict[str, Any]) -> PullRequest | None:
    repository = evaluation.trigger.repository
    if repository is None:
        return None
    number: int | None = args.get("number", evaluation.bindings.get(path))
    if number is None:
        number = await evaluation.uow.first_matching_pull(repository, args["branch_glob"])
        if number is None:
            return None
        evaluation.bindings[path] = number
    pull = await evaluation.uow.pull_request(repository, number)
    return pull if pull is not None and pull.complete else None


async def _pr_merged(evaluation: Evaluation, path: str, args: dict[str, Any]) -> bool:
    pull = await _select_pr(evaluation, path, args)
    return pull is not None and pull.state == "merged"


def _passed(fields: dict[str, Any]) -> bool:
    return fields["status"] == "completed" and fields.get("conclusion") == "success"


async def _pr_green(evaluation: Evaluation, path: str, args: dict[str, Any]) -> bool:
    pull = await _select_pr(evaluation, path, args)
    if pull is None:
        return False
    checks = await evaluation.uow.github_resource(pull.repository, CHECKS, pull.head_sha)
    if checks is None or not checks.complete:
        return False
    attempts = checks_for_head(checks, pull.head_sha)
    if not attempts:
        return False
    return all(_passed(check.fields) for check in attempts)


async def _pr_threads_resolved(evaluation: Evaluation, path: str, args: dict[str, Any]) -> bool:
    pull = await _select_pr(evaluation, path, args)
    if pull is None:
        return False
    threads = await evaluation.uow.github_resource(pull.repository, THREADS, str(pull.number))
    return (
        threads is not None
        and threads.complete
        and threads.attributes.get("head_sha") == pull.head_sha
        and type(threads.attributes.get("pull_version")) is int
        and threads.attributes["pull_version"] == pull.version
        and not unresolved_threads(threads)
    )


async def _issue_closed(evaluation: Evaluation, _path: str, args: dict[str, Any]) -> bool:
    if (repository := evaluation.trigger.repository) is None:
        return False
    issues = await evaluation.uow.github_resource(repository, ISSUES)
    key = f"issue/{args['number']}"
    return (
        issues is not None
        and issues.complete
        and any(row.key == key and row.fields["state"] == "closed" for row in issues.records)
    )


def _validate_main(args: dict[str, Any]) -> None:
    if "sha" in args:
        try:
            validate_report_sha(args["sha"])
        except ValueError as exc:
            raise LedgerError("invalid", str(exc)) from None


async def _main_green_with_images(
    evaluation: Evaluation, _path: str, args: dict[str, Any], *, image_jobs: tuple[str, ...]
) -> bool:
    repository = evaluation.trigger.repository
    if repository is None or not image_jobs:
        return False
    main = await evaluation.uow.github_resource(repository, MAIN)
    if main is None or not main.complete:
        return False
    tip = main.attributes.get("head_sha")
    verdict = main_verdict(main, require_attribution=True)
    if (
        not tip
        or args.get("sha", tip) != tip
        or verdict is None
        or verdict.fields["head_sha"] != tip
        or not _passed(verdict.fields)
    ):
        return False
    jobs = {job["name"]: job for job in sorted(verdict.fields["jobs"], key=lambda job: job["id"])}
    return all(name in jobs and _passed(jobs[name]) for name in image_jobs)


def _validate_run(args: dict[str, Any]) -> None:
    validate_identity(args["run_id"], "run_id")


async def _run_finished(evaluation: Evaluation, _path: str, args: dict[str, Any]) -> bool:
    event = await evaluation.uow.latest_run_report(args["run_id"])
    return event is not None and bool(event.fields["finished"])


def _validate_worker(args: dict[str, Any]) -> None:
    role = f"worker:{args['worker']}"
    try:
        role_family(role)
    except ValueError:
        raise LedgerError("invalid", "worker must be an exact worker slug") from None


async def _message_from(evaluation: Evaluation, path: str, args: dict[str, Any]) -> bool:
    fields = {"worker": args["worker"]}
    relay = await evaluation.pending_event(path, "switchboard.message", fields)
    session = await evaluation.pending_event(path, "claude.session_message", fields)
    return relay or session


def _validate_deploy(args: dict[str, Any]) -> None:
    try:
        validate_deploy_environment(args["environment"])
        validate_report_sha(args["sha"])
    except ValueError as exc:
        raise LedgerError("invalid", str(exc)) from None


async def _deploy_finished(evaluation: Evaluation, path: str, args: dict[str, Any]) -> bool:
    return await evaluation.pending_event(path, DEPLOY_STATUS, {**args, "finished": True})


def _validate_lane(args: dict[str, Any]) -> None:
    validate_identity(args["lane"], "lane")


async def _checklist_empty(evaluation: Evaluation, path: str, args: dict[str, Any]) -> bool:
    lane = await evaluation.uow.find_lane(args["lane"])
    if lane is None or lane.state == "archived":
        return False
    return await evaluation.pending_event(path, CHECKLIST_EMPTY, {"lane": lane.id})


async def _lane_idle(
    evaluation: Evaluation, _path: str, args: dict[str, Any], *, idle_minutes: int
) -> bool:
    lane = await evaluation.uow.find_lane(args["lane"])
    if lane is None or lane.state == "archived":
        return False
    last = await evaluation.uow.lane_activity(lane.id)
    tick = await evaluation.uow.latest_event("core.tick")
    if last is None or tick is None or tick.seq <= last.seq:
        return False
    elapsed = datetime.fromisoformat(tick.observed_at) - datetime.fromisoformat(last.observed_at)
    return elapsed.total_seconds() >= idle_minutes * 60


def build_leaf_registry(
    *, image_jobs: tuple[str, ...] = (), lane_idle_minutes: int = 15
) -> LeafRegistry:
    registry = LeafRegistry()
    for name, evaluate in (
        ("pr_merged", _pr_merged),
        ("pr_green", _pr_green),
        ("pr_threads_resolved", _pr_threads_resolved),
    ):
        registry.register(LeafSpec(name, _PR_ARGS, "level", _validate_pr_selector, evaluate, True))
    registry.register(
        LeafSpec("issue_closed", (_NUMBER,), "level", _validate_number, _issue_closed, True)
    )
    registry.register(
        LeafSpec(
            "main_green_with_images",
            (replace(_SHA, required=False),),
            "level",
            _validate_main,
            partial(_main_green_with_images, image_jobs=image_jobs),
            True,
        )
    )
    registry.register(LeafSpec("run_finished", (RUN_ID,), "level", _validate_run, _run_finished))
    registry.register(
        LeafSpec("message_from", (_WORKER,), "event", _validate_worker, _message_from)
    )
    registry.register(
        LeafSpec("deploy_finished", (_ENV, _SHA), "event", _validate_deploy, _deploy_finished)
    )
    registry.register(
        LeafSpec("checklist_empty", (_LANE,), "event", _validate_lane, _checklist_empty)
    )
    registry.register(
        LeafSpec(
            "lane_idle",
            (_LANE,),
            "level",
            _validate_lane,
            partial(_lane_idle, idle_minutes=lane_idle_minutes),
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
    mode = args.get("mode", "once")
    if mode not in ("once", "every"):
        raise LedgerError("invalid", "mode must be once or every")
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
        mode=mode,
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
