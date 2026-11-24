"""Read-only facts that the goal evaluator can use from one project snapshot."""

from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import TypeGuard

from plotroom.core.github_projection import ISSUE_PAGINATION_VERIFIED, GitHubResource
from plotroom.core.lanes import Lane


@dataclass(frozen=True)
class GoalIssue:
    number: int
    title: str
    state: str
    milestone: int | None
    labels: frozenset[str]
    assignees: frozenset[str]
    # None means relationship evidence is unavailable. A foreign reference remains
    # in the tuple so the evaluator can distinguish it from a known empty list.
    blockers: tuple[int | str, ...] | None
    lanes: frozenset[str]
    decisions: frozenset[str]


@dataclass(frozen=True)
class GoalFacts:
    repository: str
    issues: Mapping[int, GoalIssue]
    human_login: str | None
    as_of: str | None = None
    unresolved_lanes: frozenset[str] = frozenset()


@dataclass(frozen=True)
class StoredGoal:
    name: str
    text: str
    updated_at: str
    uses_blockers: bool = False


def verified_issue_projection(resource: GitHubResource | None) -> TypeGuard[GitHubResource]:
    """Only the verified page walk can establish complete goal evidence."""
    return (
        resource is not None
        and resource.complete
        and resource.attributes.get(ISSUE_PAGINATION_VERIFIED) is True
    )


def issue_number(repository: str, ref: str) -> int | None:
    """Resolve numeric or GitHub-qualified refs against this project only."""
    owner, separator, number = ref.rpartition("#")
    if separator and owner and owner.casefold() != repository.casefold():
        return None
    if not number.isascii() or not number.isdecimal() or len(number) > 19:
        return None
    result = int(number)
    return result if 0 < result <= 2**63 - 1 else None


def _links(repository: str, entries: Iterable[tuple[str, Iterable[str]]]) -> dict[int, set[str]]:
    result: dict[int, set[str]] = defaultdict(set)
    for identifier, refs in entries:
        for ref in refs:
            if (number := issue_number(repository, ref)) is not None:
                result[number].add(identifier)
    return result


def project_goal_facts(
    repository: str,
    human_login: str | None,
    issues: GitHubResource,
    tree: GitHubResource | None,
    lanes: Iterable[Lane],
    decisions: Iterable[tuple[str, Iterable[str]]],
) -> GoalFacts:
    """Join the existing issue, relationship, lane and decision projections."""
    assignees: dict[int, frozenset[str]] = {
        record.fields["number"]: frozenset(login.casefold() for login in record.fields["assignees"])
        for record in issues.records
        if record.kind == "issue_assignees"
    }
    metadata = (
        {
            record.fields["number"]: record
            for record in tree.records
            if record.kind == "issue_metadata"
        }
        if verified_issue_projection(tree)
        else {}
    )
    lane_refs: list[tuple[str, list[str]]] = []
    unresolved_lanes: set[str] = set()
    for lane in lanes:
        if lane.state == "archived":
            continue
        confirmed: list[str] = []
        for ref in lane.work_items:
            if "#" not in ref:
                unresolved_lanes.add(lane.id)
            else:
                confirmed.append(ref)
        lane_refs.append((lane.id, confirmed))
    lane_links = _links(repository, lane_refs)
    decision_links = _links(repository, decisions)
    projected: dict[int, GoalIssue] = {}
    for record in issues.records:
        if record.details.get("resource") != "issue":
            continue
        number: int = record.fields["number"]
        details = record.details
        relation = metadata.get(number)
        blockers: tuple[int | str, ...] | None = None
        updated_at = details.get("updated_at")
        if updated_at and relation is not None and relation.details.get("updated_at") == updated_at:
            blockers = tuple(relation.fields["blocked_by"])
        projected[number] = GoalIssue(
            number=number,
            title=details["title"],
            state=record.fields["state"],
            milestone=details["milestone"],
            labels=frozenset(details["labels"]),
            assignees=assignees.get(number, frozenset()),
            blockers=blockers,
            lanes=frozenset(lane_links.get(number, ())),
            decisions=frozenset(decision_links.get(number, ())),
        )
    return GoalFacts(
        repository,
        projected,
        human_login.casefold() if human_login else None,
        issues.as_of or None,
        frozenset(unresolved_lanes),
    )
