"""Convert local event and projection sources to stable searchable records."""

from __future__ import annotations

from typing import TYPE_CHECKING

from plotroom.core.intake import SESSION_MESSAGE, TURN_REPORT
from plotroom.core.merge_flakes import FLAKE_EVENT
from plotroom.ports.search import (
    ASK_KIND,
    DECISION_KIND,
    ISSUE_COMMENT_KIND,
    MESSAGE_KIND,
    PR_BODY_KIND,
    SearchRecord,
)

if TYPE_CHECKING:
    from plotroom.core.ask_checks import Ask
    from plotroom.core.events import Event
    from plotroom.core.github import PullRequest
    from plotroom.core.github_projection import GitHubRecord, GitHubResource
    from plotroom.core.human_queue import Decision

EVENT_KINDS = frozenset({MESSAGE_KIND, SESSION_MESSAGE, TURN_REPORT, FLAKE_EVENT})
PRIVATE_KINDS = frozenset({DECISION_KIND, ASK_KIND})
SEARCH_KINDS = EVENT_KINDS | PRIVATE_KINDS | {ISSUE_COMMENT_KIND, PR_BODY_KIND}
FLAKE_TEXT_FIELDS = (
    "check_name",
    "pattern",
    "test_id",
    "path",
    "repository",
    "head_sha",
    "check_id",
)


def event_record(event: Event) -> SearchRecord:
    body = event.body
    if event.kind == FLAKE_EVENT:
        body = "\n".join(
            [body, *(str(event.fields[key]) for key in FLAKE_TEXT_FIELDS if key in event.fields)]
        ).lstrip("\n")
    return SearchRecord(event.kind, event.id, body)


def decision_record(decision: Decision) -> SearchRecord:
    return SearchRecord(DECISION_KIND, decision.decision_id, decision.exact_words)


def ask_record(ask: Ask) -> SearchRecord:
    return SearchRecord(ASK_KIND, ask.ask_id, ask.text)


def comment_record(repository: str, comment: GitHubRecord) -> SearchRecord:
    record_id = f"{repository}#{comment.fields['number']}#issuecomment-{comment.fields['id']}"
    return SearchRecord(ISSUE_COMMENT_KIND, record_id, comment.body)


def pull_body_record(
    repository: str, number: int, body: str, *, provisional: bool = False
) -> SearchRecord:
    return SearchRecord(PR_BODY_KIND, f"{repository}#{number}", body, provisional=provisional)


def pull_text_record(
    pull: PullRequest,
    resource: GitHubResource,
    *,
    updated_since: str | None = None,
    allow_incomplete: bool = False,
) -> SearchRecord | None:
    attributes = resource.attributes
    body = attributes.get("body")
    updated_at = attributes.get("pull_updated_at")
    if (
        (resource.complete or allow_incomplete)
        and isinstance(body, str)
        and isinstance(updated_at, str)
        and attributes.get("head_sha") == pull.head_sha
        and type(attributes.get("pull_version")) is int
        and attributes["pull_version"] == pull.version
        and (updated_since if updated_since is not None else pull.updated_at)
        <= updated_at
        <= pull.updated_at
    ):
        return pull_body_record(
            pull.repository,
            pull.number,
            body,
            provisional=not pull.complete or updated_at != pull.updated_at,
        )
    return None
