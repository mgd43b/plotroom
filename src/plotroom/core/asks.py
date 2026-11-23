"""Record projected GitHub asks and reconcile their state-backed next items."""

from __future__ import annotations

import uuid
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from plotroom.core.ask_checks import ASK_ITEMS, Ask, ask_conditions, comment_time
from plotroom.core.events import LedgerError, format_time
from plotroom.core.github_projection import (
    ASK_COMMENT_HISTORY,
    ISSUES,
    GitHubRecord,
    ask_history_version,
    projected_comment,
)

if TYPE_CHECKING:
    from plotroom.core.unit_of_work import UnitOfWork


async def record_ask(
    uow: UnitOfWork, args: dict[str, Any], *, repository: str | None
) -> dict[str, Any]:
    if repository is None:
        raise LedgerError("unavailable", "the project has no GitHub issue projection")
    issue: int = args["issue"]
    comment_id: int = args["comment_id"]
    text: str = args["text"]
    options: list[Any] = args["options"]
    if not (0 < issue < 2**63 and 0 < comment_id < 2**63):
        raise LedgerError("invalid", "issue and comment_id must be positive GitHub numbers")
    if not text.strip() or not all(
        isinstance(option, str) and option.strip() for option in options
    ):
        raise LedgerError("invalid", "ask text and each option must be nonempty text")
    try:
        for option in options:
            option.encode("utf-8")
    except UnicodeEncodeError:
        raise LedgerError("invalid", "ask options must be valid UTF-8 text") from None
    comment = await projected_comment(uow, repository, issue, comment_id)
    if text not in comment.body:
        raise LedgerError("invalid", "ask text does not occur in the projected comment")
    posted = comment_time(comment.fields["created_at"])
    if posted is None:
        raise LedgerError("unavailable", "the projected comment has no usable creation time")
    existing = await uow.ask_for_comment(repository, comment_id)
    if existing is None:
        record = Ask(
            f"ask_{uuid.uuid7()}",
            repository,
            issue,
            text,
            tuple(options),
            comment_id,
            format_time(posted),
            format_time(uow.now()),
        )
    else:
        record = replace(existing, text=text, options=tuple(options))
        if existing == record:
            return {"ask_id": existing.ask_id}
        if existing.settled_by is not None:
            raise LedgerError("conflict", "the ask has already been settled")
    await uow.save_ask(record)
    return {"ask_id": record.ask_id}


async def reconcile_asks(
    uow: UnitOfWork, repository: str | None, human_login: str | None, stale_days: int
) -> None:
    if repository is None:
        return
    asks = await uow.unsettled_asks(repository)
    if not asks:
        return
    numbers = {ask.issue for ask in asks}
    issues = await uow.github_resource(repository, ISSUES)
    issue_rows: dict[int, GitHubRecord] = {}
    if issues is not None and issues.complete:
        issue_rows = {
            row.fields["number"]: row
            for row in issues.records
            if row.details.get("resource") == "issue"
        }
    comments = await uow.ask_comments(repository, numbers)
    now = uow.now()
    complete: dict[int, bool] = {}
    for number, row in issue_rows.items():
        if number in numbers and (coverage := ask_history_version(row)) is not None:
            version, _ = coverage
            cursor = await uow.fetch_cursor(repository, f"{ASK_COMMENT_HISTORY}{number}")
            complete[number] = cursor is not None and cursor.watermark == version
    for ask in asks:
        issue = issue_rows.get(ask.issue)
        conditions = ask_conditions(
            ask,
            comments.get(ask.issue, []),
            issue_state=issue.fields["state"] if issue is not None else None,
            history_complete=complete.get(ask.issue, False),
            human_login=human_login,
            stale_days=stale_days,
            now=now,
        )
        for kind, applies in conditions.items():
            if applies is True:
                await uow.raise_item(
                    kind,
                    ask.ask_id,
                    "orchestrator",
                    {
                        "ask_id": ask.ask_id,
                        "repository": ask.repository,
                        "issue": ask.issue,
                        "comment_id": ask.comment_id,
                    },
                )
            elif applies is False:
                await uow.clear_item(kind, ask.ask_id)


async def settle_asks(uow: UnitOfWork, asks: list[Ask], decision_id: str) -> None:
    for ask in asks:
        await uow.save_ask(replace(ask, settled_by=decision_id))
        for kind in ASK_ITEMS:
            await uow.clear_item(kind.name, ask.ask_id)
