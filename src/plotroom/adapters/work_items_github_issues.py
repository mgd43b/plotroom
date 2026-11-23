"""Read ready BMad tickets from the project's GitHub Issues projection."""

from __future__ import annotations

import asyncio
import re
import tomllib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import yaml

from plotroom.ports.work_items import MAX_WORK_ITEM_BODY_BYTES, WorkItem

_LEAF_TYPES = ("story", "spike", "bug")
_STATES = ("backlog", "in-progress", "review", "done", "dropped")
_CONFIG_LIMIT = 128 * 1024
_BODY_LIMIT = 128 * 1024
_METADATA_LIMIT = 8 * 1024
_METADATA_START = re.compile(r"\A```yaml[ \t]*\r?\n")
_METADATA_END = re.compile(r"^```[ \t]*\r?(?:\n|\Z)", re.M)
_UNKNOWN = re.compile(r"^[ \t]*(?:[-*][ \t]+)?Unknown:[ \t]*\S", re.M)

type IssueSnapshot = Callable[[], Awaitable[tuple[dict[str, Any], ...]]]


class GitHubIssuesError(ValueError):
    """The projection or ticket-store configuration cannot supply ready issues."""


@dataclass(frozen=True)
class _Issue:
    number: int
    title: str
    kind: str | None
    status: str | None
    can_start: bool
    milestone: int | None
    children: tuple[int, ...]
    blockers: tuple[int | str, ...]


def _settings(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as file:
            raw = file.read(_CONFIG_LIMIT + 1)
        if len(raw) > _CONFIG_LIMIT:
            raise ValueError("ticket-store configuration is too large")
        value = tomllib.loads(raw.decode("utf-8-sig"))["tickets"]
        if not isinstance(value, dict):
            raise ValueError("expected a GitHub ticket store")
        table = cast("dict[str, Any]", value)
        if table.get("store") != "github":
            raise ValueError("expected a GitHub ticket store")
        return table
    except KeyError, OSError, ValueError:
        raise GitHubIssuesError("cannot read the BMad GitHub ticket-store configuration") from None


def _mapping(base: dict[str, Any], overrides: dict[str, Any], key: str) -> dict[str, str]:
    default, custom = base.get(key), overrides.get(key, {})
    if not isinstance(default, dict) or not isinstance(custom, dict):
        raise GitHubIssuesError(f"BMad tickets.{key} must be a table")
    values = cast("dict[str, Any]", default) | cast("dict[str, Any]", custom)
    normalized: dict[str, str] = {}
    for name, value in values.items():
        if not isinstance(value, str):
            raise GitHubIssuesError(f"BMad tickets.{key} values must be strings")
        normalized[name] = value.strip().casefold()
    return normalized


@dataclass(frozen=True)
class _StoreMapping:
    types: dict[str, str]
    statuses: dict[str, str]

    @classmethod
    def load(cls, starter: Path, project: Path, repository: str) -> _StoreMapping:
        base, overrides = _settings(starter), _settings(project)
        repo = overrides.get("repo", base.get("repo", ""))
        if not isinstance(repo, str) or (repo and repo.casefold() != repository.casefold()):
            raise GitHubIssuesError("BMad repository must match the configured GitHub repository")
        types = _mapping(base, overrides, "types")
        statuses = _mapping(base, overrides, "status")
        previous = ""
        for state in _STATES:
            # A blank tracker status inherits the preceding state. On a read with no
            # recorded BMad state, the earliest match wins.
            previous = statuses[state] = statuses.get(state) or previous
        if not statuses["backlog"]:
            raise GitHubIssuesError("BMad tickets.status requires a backlog label")
        return cls(types, statuses)

    def kind(self, labels: frozenset[str]) -> str | None:
        matches = [kind for kind in ("epic", *_LEAF_TYPES) if self.types.get(kind) in labels]
        return matches[0] if len(matches) == 1 else None

    def status(self, state: str, reason: str | None, labels: frozenset[str]) -> str | None:
        if state == "closed":
            closed_as = f"closed: {(reason or '').casefold().replace('_', ' ')}"
            for value in (closed_as, "closed"):
                for status in ("done", "dropped"):
                    if self.statuses[status] == value:
                        return status
            return None
        matches = [(key, self.statuses[key]) for key in _STATES[:3] if self.statuses[key] in labels]
        return matches[0][0] if len({label for _, label in matches}) == 1 else None


def _number(value: Any) -> int:
    if type(value) is not int or not 0 < value <= 2**63 - 1:
        raise GitHubIssuesError("the GitHub issue snapshot has an invalid number")
    return value


def _list(value: Any) -> list[Any]:
    if not isinstance(value, list):
        raise GitHubIssuesError("the GitHub issue snapshot is missing a list")
    return cast("list[Any]", value)


def _without_comments(body: str) -> str:
    parts: list[str] = []
    position = 0
    while (start := body.find("<!--", position)) != -1:
        end = body.find("-->", start + 4)
        if end == -1:
            break
        parts.append(body[position:start])
        position = end + 3
    parts.append(body[position:])
    return "".join(parts)


def _body_allows_start(kind: str, body: str) -> bool:
    if len(body) > _BODY_LIMIT:
        return False
    refined: bool | None = None
    if start := _METADATA_START.match(body):
        limit = start.end() + _METADATA_LIMIT
        end = _METADATA_END.search(body, start.end(), limit)
        if end is None or (end.end() == limit < len(body) and not end.group().endswith("\n")):
            return False
        try:
            # Compose syntax nodes without constructing YAML values or expanding aliases.
            metadata = cast(
                "yaml.Node | None",
                yaml.compose(  # pyright: ignore[reportUnknownMemberType]
                    body[start.end() : end.start()], Loader=yaml.SafeLoader
                ),
            )
        except yaml.YAMLError, RecursionError:
            return False
        if not isinstance(metadata, yaml.MappingNode):
            return False
        for key, value in metadata.value:
            if isinstance(key, yaml.ScalarNode) and key.value == "refined":
                if refined is not None or not isinstance(value, yaml.ScalarNode):
                    return False
                flag = value.value.casefold()
                if flag not in ("true", "false"):
                    return False
                refined = flag == "true"
        body = body[end.end() :]
    return (refined if refined is not None else kind != "bug") and not _UNKNOWN.search(
        _without_comments(body)
    )


def _issue(row: dict[str, Any], mapping: _StoreMapping) -> _Issue:
    title, state, reason = row.get("title"), row.get("state"), row.get("state_reason")
    body = row.get("body")
    labels = _list(row.get("labels"))
    if (
        not isinstance(title, str)
        or state not in ("open", "closed")
        or (reason is not None and not isinstance(reason, str))
        or (body is not None and not isinstance(body, str))
        or not all(isinstance(label, str) and label for label in labels)
    ):
        raise GitHubIssuesError("the GitHub issue snapshot has an invalid issue")
    normalized = frozenset(label.casefold() for label in cast("list[str]", labels))
    milestone = row.get("milestone")
    kind = mapping.kind(normalized)
    status = mapping.status(cast("str", state), reason, normalized)
    return _Issue(
        _number(row.get("number")),
        title,
        kind,
        status,
        status == "backlog" and kind in _LEAF_TYPES and _body_allows_start(kind, body or ""),
        _number(milestone) if milestone is not None else None,
        tuple(_number(child) for child in _list(row.get("sub_issues"))),
        tuple(
            blocker if isinstance(blocker, str) and blocker else _number(blocker)
            for blocker in _list(row.get("blocked_by"))
        ),
    )


class GitHubIssuesWorkItems:
    """Classify tickets from a complete, committed issue and milestone snapshot."""

    def __init__(
        self,
        read: IssueSnapshot,
        starter: Path,
        project_config: Path,
        repository: str,
        milestone: int | None = None,
    ) -> None:
        self._read = read
        self._starter = starter
        self._project_config = project_config
        self._repository = repository
        self._milestone = milestone

    async def ready_items(self) -> tuple[WorkItem, ...]:
        rows = await self._read()
        return await asyncio.to_thread(self._classify, rows)

    async def body(self, ref: str) -> str | None:
        if re.fullmatch(r"[1-9][0-9]{0,18}", ref) is None:
            return None
        number = int(ref)
        matches = [
            row
            for row in await self._read()
            if row.get("resource") == "issue" and _number(row.get("number")) == number
        ]
        if not matches:
            return None
        if len(matches) != 1:
            raise GitHubIssuesError("the GitHub issue snapshot has duplicate issues")
        body = matches[0].get("body")
        if body is None:
            return ""
        if not isinstance(body, str) or len(body.encode("utf-8")) > MAX_WORK_ITEM_BODY_BYTES:
            raise GitHubIssuesError("the GitHub issue snapshot has an invalid body")
        return body

    def _classify(self, rows: tuple[dict[str, Any], ...]) -> tuple[WorkItem, ...]:
        mapping = _StoreMapping.load(self._starter, self._project_config, self._repository)
        milestones: dict[int, str] = {}
        issues: dict[int, _Issue] = {}
        for row in rows:
            if row.get("resource") == "milestone":
                number, state = _number(row.get("number")), row.get("state")
                if state not in ("open", "closed") or number in milestones:
                    raise GitHubIssuesError("the GitHub issue snapshot has an invalid milestone")
                milestones[number] = cast("str", state)
            elif row.get("resource") == "issue":
                issue = _issue(row, mapping)
                if issue.number in issues:
                    raise GitHubIssuesError("the GitHub issue snapshot has duplicate issues")
                issues[issue.number] = issue

        if self._milestone is not None and self._milestone not in milestones:
            raise GitHubIssuesError("the configured GitHub milestone is unavailable")

        parents: dict[int, tuple[_Issue, int]] = {}
        for epic in issues.values():
            if epic.kind == "epic":
                for position, child in enumerate(epic.children):
                    if child in parents:
                        raise GitHubIssuesError("a GitHub sub-issue occurs more than once")
                    parents[child] = (epic, position)

        done = {
            issue.number
            for issue in issues.values()
            if issue.status == "done" and issue.kind is not None
        }
        met = done | {
            issue.number
            for issue in issues.values()
            if issue.status == "review" and issue.kind in _LEAF_TYPES
        }
        ready: list[tuple[tuple[int, bool, int, int], WorkItem]] = []
        for issue in issues.values():
            if not issue.can_start:
                continue
            parent = parents.get(issue.number)
            epic = parent[0] if parent else None
            milestone = issue.milestone or (epic.milestone if epic else None)
            if self._milestone is not None and milestone != self._milestone:
                continue
            if milestone is not None and milestones.get(milestone) != "open":
                continue
            if not met.issuperset(issue.blockers) or (epic and not done.issuperset(epic.blockers)):
                continue
            order = (
                milestone or 0,
                epic is None,
                epic.number if epic else issue.number,
                parent[1] if parent else 0,
            )
            ready.append((order, WorkItem(str(issue.number), issue.title)))
        return tuple(item for _, item in sorted(ready, key=lambda entry: entry[0]))
