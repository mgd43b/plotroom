"""Durable GitHub observations applied by the project's single writer."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from plotroom.core.events import FieldSpec, LedgerError, NewEvent
from plotroom.core.lanes import infer_lane

if TYPE_CHECKING:
    from plotroom.core.unit_of_work import UnitOfWork


CHECKS = "checks"
MAIN = "main_runs"
MAIN_CI = "main_ci"
MAIN_CI_WINDOW = 30
THREADS = "review_threads"
FILES = "pull_files"
LABELS = "pull_labels"
PULL_TEXT = "pull_text"
ISSUES = "issues"
COMMENTS = "issue_comments"
COMMENT_BACKFILL = "issue_comments_backfill"
ASK_COMMENT_HISTORY = "ask_comment_history"
ISSUE_TREE = "issue_tree"
ISSUE_PAGINATION_VERIFIED = "pagination_verified"
ISSUE_RELATION_RETRY = "relationship_retry"

REPOSITORY = FieldSpec("repository", "str")
NUMBER = FieldSpec("number", "int")
HEAD = FieldSpec("head_sha", "str")
STATUS = FieldSpec("status", "str")
CONCLUSION = FieldSpec("conclusion", "str", required=False)
NAME = FieldSpec("name", "str")
GITHUB_FIELDS = {
    "check_run": (
        FieldSpec("id", "int"),
        HEAD,
        NAME,
        STATUS,
        CONCLUSION,
        FieldSpec("number", "int", required=False),
    ),
    "workflow_run": (
        FieldSpec("id", "int"),
        FieldSpec("attempt", "int"),
        HEAD,
        NAME,
        STATUS,
        CONCLUSION,
        FieldSpec("jobs", "array"),
    ),
    "review_thread": (
        FieldSpec("id", "str"),
        NUMBER,
        FieldSpec("resolved", "bool"),
        FieldSpec("path", "str"),
        FieldSpec("line", "int", required=False),
    ),
    "pull_file": (
        NUMBER,
        HEAD,
        FieldSpec("path", "str"),
        STATUS,
        FieldSpec("previous_path", "str", required=False),
    ),
    "pull_label": (
        NUMBER,
        NAME,
        FieldSpec("actor", "str", required=False),
        FieldSpec("action", "str"),
        FieldSpec("id", "int"),
    ),
    "issue_opened": (NUMBER, FieldSpec("state", "str")),
    "issue_closed": (NUMBER, FieldSpec("state", "str")),
    "issue_reopened": (NUMBER, FieldSpec("state", "str")),
    "issue_assignees": (NUMBER, FieldSpec("assignees", "array")),
    "issue_comment": (
        NUMBER,
        FieldSpec("id", "int"),
        FieldSpec("author", "str", required=False),
        FieldSpec("created_at", "str"),
        FieldSpec("updated_at", "str"),
    ),
    "issue_metadata": (
        NUMBER,
        FieldSpec("labels", "array"),
        FieldSpec("milestone", "int", required=False),
        FieldSpec("sub_issues", "array"),
        FieldSpec("blocked_by", "array"),
    ),
    "milestone": (NUMBER, FieldSpec("state", "str"), FieldSpec("title", "str")),
}


@dataclass(frozen=True)
class GitHubRecord:
    key: str
    kind: str
    fields: dict[str, Any]
    body: str = ""
    # Raw projection fields can evolve without a distinct event (e.g. a workflow's jobs).
    details: dict[str, Any] = field(default_factory=lambda: {})
    fact: str = ""


@dataclass(frozen=True)
class GitHubResource:
    records: tuple[GitHubRecord, ...]
    complete: bool = True
    attributes: dict[str, Any] = field(default_factory=lambda: {})
    generation: int = 0
    as_of: str = ""


async def apply_resource(
    uow: UnitOfWork,
    repository: str,
    resource: str,
    scope: str,
    result: GitHubResource,
    as_of: str,
    *,
    pulls: tuple[int, ...] = (),
) -> None:
    current = await uow.github_resource(repository, resource, scope)
    previous = {row.key: row for row in current.records} if current else {}
    generation = current.generation + 1 if current else 1
    fresh = {row.key: row for row in result.records}
    bindings: dict[int, str | None] = {}
    if resource == FILES:
        pulls = (int(scope),)
    candidates = set(pulls)
    if resource == CHECKS:
        candidates.update(
            number for row in fresh.values() for number in row.details["pull_numbers"]
        )
    for number in sorted(candidates):
        pr = await uow.pull_request(repository, number)
        if pr is None and resource == FILES:
            return
        if pr is None or (resource == CHECKS and scope != pr.head_sha):
            continue
        if resource == FILES and result.complete and result.attributes["head_sha"] != pr.head_sha:
            # The PR poll committed a new head while the file request was in flight.
            return
        bindings[number] = (
            await infer_lane(uow, branch=pr.branch) if pr.head_repository == repository else None
        )
    # The gate's workflow-scoped history is a read model. Workflow events still
    # come from the existing all-workflow monitor.
    for row in () if resource == MAIN_CI else fresh.values():
        prior = previous.get(row.key)
        if prior is not None and (
            row.fact == prior.fact
            if row.fact
            else (row.fields, row.body) == (prior.fields, prior.body)
        ):
            continue
        kind = row.kind
        if kind == "issue_opened" and prior is not None:
            kind = "issue_reopened"
        fields = {"repository": repository, **row.fields}
        selected = row.details.get("pull_numbers") or pulls
        candidates = [(number, bindings[number]) for number in selected if number in bindings]
        with_lane = [(number, lane) for number, lane in candidates if lane is not None]
        lane_id = None
        relevant = with_lane or candidates
        if len(relevant) == 1:
            fields["number"], lane_id = relevant[0]
        uid = f"{repository}:{resource}:{scope}:{row.key}@{row.fact or generation}"
        if kind in {
            "check_run",
            "review_thread",
            "issue_opened",
            "issue_closed",
            "issue_reopened",
        }:
            uid = f"{uid}:{generation}"
        await uow.append(NewEvent("github", f"github.{kind}", uid, fields, row.body, lane_id))
    records = fresh if result.complete else {**previous, **fresh}
    attributes = (
        result.attributes
        if result.complete
        else {**(current.attributes if current else {}), **result.attributes}
    )
    await uow.save_github_resource(
        repository,
        resource,
        scope,
        GitHubResource(tuple(records.values()), result.complete, attributes, generation, as_of),
    )
    if (
        resource == FILES
        and result.complete
        and (
            current is None
            or current.attributes.get("head_sha") != attributes.get("head_sha")
            or previous != fresh
        )
    ):
        uow.pull_overlaps_changed()


def projected_issues(resource: GitHubResource | None) -> dict[int, GitHubRecord]:
    if resource is None or not resource.complete:
        return {}
    return {
        row.fields["number"]: row
        for row in resource.records
        if row.details.get("resource") == "issue"
    }


def ask_history_version(issue: GitHubRecord) -> tuple[str, int] | None:
    count = issue.details.get("comment_count")
    updated = issue.details.get("updated_at")
    if (
        issue.kind == "issue_closed"
        and type(count) is int
        and count >= 0
        and isinstance(updated, str)
        and updated
    ):
        return f"{updated}/{count}", count
    return None


async def projected_comment(
    uow: UnitOfWork, repository: str, issue: int, comment_id: int
) -> GitHubRecord:
    scope = str(comment_id)
    resource = await uow.github_resource(repository, COMMENTS, scope)
    if resource is None or not resource.complete or not resource.records:
        raise LedgerError(
            "unavailable", "the cited comment is not projected yet; retry after polling"
        )
    if len(resource.records) == 1:
        (comment,) = resource.records
        if (
            comment.kind == "issue_comment"
            and comment.key == scope
            and type(comment.fields.get("id")) is int
            and comment.fields["id"] == comment_id
            and type(comment.fields.get("number")) is int
            and comment.fields["number"] == issue
        ):
            return comment
    raise LedgerError("invalid", "the projected comment does not match the cited issue and ID")


def latest_checks(resource: GitHubResource) -> tuple[GitHubRecord, ...]:
    latest: dict[str, GitHubRecord] = {}
    for check in sorted(resource.records, key=lambda item: int(item.fields["id"])):
        latest[check.fields["name"]] = check
    return tuple(latest.values())


def checks_for_head(resource: GitHubResource, head_sha: str) -> tuple[GitHubRecord, ...] | None:
    """Select latest attempts by check name and app for this head."""
    latest: dict[tuple[str, int | None, int | None], GitHubRecord] = {}
    for check in resource.records:
        fields = check.fields
        if (
            check.kind != "check_run"
            or type(fields.get("id")) is not int
            or fields["id"] < 1
            or not isinstance(fields.get("name"), str)
            or not fields["name"]
            or fields.get("head_sha") != head_sha
        ):
            return None
        app_id = check.details.get("app_id")
        # Unidentified runs cannot establish that they supersede one another.
        if type(app_id) is int and app_id > 0:
            key = (fields["name"], app_id, None)
        else:
            key = (fields["name"], None, fields["id"])
        prior = latest.get(key)
        if prior is None or fields["id"] > prior.fields["id"]:
            latest[key] = check
    return tuple(latest.values())


def main_verdict(
    resource: GitHubResource, *, require_attribution: bool = False
) -> GitHubRecord | None:
    return next(
        (
            row
            for row in resource.records
            if row.fields["status"] == "completed"
            and (not require_attribution or row.details.get("main_branch_run") is True)
        ),
        None,
    )


def unresolved_threads(resource: GitHubResource) -> tuple[GitHubRecord, ...]:
    return tuple(row for row in resource.records if not row.fields["resolved"])
