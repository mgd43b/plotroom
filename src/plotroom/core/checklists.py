"""Typed READY checklists and their merge lifecycle."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from typing import TYPE_CHECKING, Any, Literal, cast

from plotroom.core.events import FieldSpec, LedgerError, NewEvent, validate_fields

if TYPE_CHECKING:
    from plotroom.core.github import PullRequest
    from plotroom.core.lanes import Lane
    from plotroom.core.unit_of_work import UnitOfWork

CHECKLIST_ARG = FieldSpec("checklist", "array", required=False)
ARCHIVE_PROPOSAL = "archive_lane"
CHECKLIST_EMPTY = "core.checklist_empty"
CHECKLIST_EMPTY_FIELDS = (FieldSpec("lane", "str"), FieldSpec("version", "int"))
_STEP_FIELDS = (FieldSpec("step", "str"), FieldSpec("owner", "str"), FieldSpec("ref", "str"))


@dataclass(frozen=True)
class ChecklistStep:
    step: str
    owner: str
    ref: str
    completion_ref: str | None = None


@dataclass(frozen=True)
class Checklist:
    lane: str
    version: int
    repository: str
    branch: str
    pr: int
    head_sha: str
    state: Literal["pending", "active", "invalidated"]
    steps: tuple[ChecklistStep, ...]

    def open_steps(self) -> tuple[ChecklistStep, ...]:
        return tuple(step for step in self.steps if step.completion_ref is None)

    def render(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "pr": self.pr,
            "head_sha": self.head_sha,
            "state": self.state,
            "steps": [asdict(step) for step in self.steps],
        }

    def matches(self, pull: PullRequest | None) -> bool:
        return (
            pull is not None
            and pull.complete
            and pull.repository == pull.head_repository == self.repository
            and (pull.branch, pull.number, pull.head_sha) == (self.branch, self.pr, self.head_sha)
        )


def _parse_steps(rows: list[Any]) -> tuple[ChecklistStep, ...]:
    steps: list[ChecklistStep] = []
    for row in rows:
        if not isinstance(row, dict):
            raise LedgerError("invalid", "checklist entries must contain step, owner and ref")
        fields = cast(dict[str, Any], row)
        validate_fields(_STEP_FIELDS, fields, "checklist entry")
        step = ChecklistStep(fields["step"], fields["owner"], fields["ref"])
        if not all(value.strip() for value in (step.step, step.owner, step.ref)):
            raise LedgerError("invalid", "checklist step, owner and ref must be non-empty")
        steps.append(step)
    if len({step.step for step in steps}) != len(steps):
        raise LedgerError("invalid", "checklist step names must be unique")
    return tuple(steps)


async def declare_checklist(
    uow: UnitOfWork, lane: Lane, repository: str | None, args: dict[str, Any]
) -> None:
    current = await uow.find_checklist(lane.id)
    if current is not None and current.state == "active":
        raise LedgerError("conflict", "the lane already has an active checklist")
    if "checklist" not in args:
        return
    steps = _parse_steps(args["checklist"])
    if repository is None:
        raise LedgerError("conflict", "a READY checklist requires a GitHub projection")
    version = current.version if current is not None else 0
    pending = Checklist(
        lane.id, version, repository, lane.branch, args["pr"], args["head_sha"], "pending", steps
    )
    if pending != current:
        await uow.save_checklist(replace(pending, version=version + 1))


async def invalidate_checklist(uow: UnitOfWork, checklist: Checklist | None) -> None:
    if checklist is not None and checklist.state == "pending":
        await uow.save_checklist(replace(checklist, state="invalidated"))


async def activate_checklist(uow: UnitOfWork, checklist: Checklist) -> None:
    await uow.save_checklist(replace(checklist, state="active"))
    if not checklist.steps:
        await propose_lane_archive(uow, checklist.lane)


async def propose_lane_archive(uow: UnitOfWork, lane: str) -> None:
    await uow.raise_item(ARCHIVE_PROPOSAL, lane, "orchestrator", {"lane": lane})


async def complete_checklist_step(
    uow: UnitOfWork, lane: str, name: str, completion_ref: str | None
) -> dict[str, Any]:
    checklist = await uow.find_checklist(lane)
    if checklist is None or checklist.state != "active":
        raise LedgerError("conflict", "the lane has no active checklist")
    if completion_ref is not None and not completion_ref.strip():
        raise LedgerError("invalid", "completion ref must be non-empty")
    for index, step in enumerate(checklist.steps):
        if step.step != name:
            continue
        if step.completion_ref is not None:
            return {"changed": False, **asdict(step)}
        done = replace(step, completion_ref=completion_ref or step.ref)
        steps = (*checklist.steps[:index], done, *checklist.steps[index + 1 :])
        updated = replace(checklist, steps=steps)
        await uow.save_checklist(updated)
        if not updated.open_steps():
            await propose_lane_archive(uow, lane)
            await uow.append(
                NewEvent(
                    "core",
                    CHECKLIST_EMPTY,
                    f"{lane}:{checklist.version}",
                    {"lane": lane, "version": checklist.version},
                    lane_id=lane,
                )
            )
        return {"changed": True, **asdict(done)}
    raise LedgerError("not_found", "checklist step not found")
