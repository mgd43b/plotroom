"""Human decisions and their verbatim evidence."""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Any

from plotroom.core.asks import settle_asks
from plotroom.core.commands import CommandError
from plotroom.core.events import FieldSpec, format_time
from plotroom.core.github_projection import projected_comment

if TYPE_CHECKING:
    from plotroom.core.ask_checks import Ask
    from plotroom.core.unit_of_work import UnitOfWork

DECISION_FIELDS = (
    FieldSpec("who", "str"),
    FieldSpec("exact_words", "str"),
    FieldSpec("where_ref", "object"),
    FieldSpec("governs", "array"),
    FieldSpec("settles", "array"),
)


@dataclass(frozen=True)
class Decision:
    decision_id: str
    who: str
    exact_words: str
    where_ref: dict[str, Any]
    governs: list[str]
    settles: list[str]
    created_at: str

    def render(self) -> dict[str, Any]:
        return asdict(self)


def _text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CommandError("invalid", f"{name} must be a non-empty string")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        raise CommandError("invalid", f"{name} must be valid UTF-8 text") from None
    return value


async def _verify_evidence(
    uow: UnitOfWork,
    who: str,
    exact_words: str,
    where_ref: dict[str, Any],
    repository: str | None,
    human_login: str | None,
) -> None:
    kind = where_ref.get("kind")
    if kind == "chat":
        if set(where_ref) != {"kind", "ref"}:
            raise CommandError("invalid", "chat citations require only kind and ref")
        _text(where_ref["ref"], "where_ref.ref")
        if human_login is not None and who.casefold() != human_login.casefold():
            raise CommandError("forbidden", "who does not match the configured human_login")
        return
    if kind != "github_comment" or set(where_ref) != {"kind", "repository", "issue", "comment_id"}:
        raise CommandError(
            "invalid", "GitHub comment citations require kind, repository, issue and comment_id"
        )
    issue, comment_id = where_ref["issue"], where_ref["comment_id"]
    if (
        type(issue) is not int
        or not 0 < issue < 2**63
        or type(comment_id) is not int
        or not 0 < comment_id < 2**63
    ):
        raise CommandError("invalid", "issue and comment_id must be positive GitHub numbers")
    if repository is None or where_ref["repository"] != repository:
        raise CommandError("invalid", "the citation must name the configured GitHub repository")
    if human_login is None or who.casefold() != human_login.casefold():
        raise CommandError("forbidden", "who must match the configured human_login")
    comment = await projected_comment(uow, repository, issue, comment_id)
    author = comment.fields.get("author")
    if not isinstance(author, str) or author.casefold() != human_login.casefold():
        raise CommandError("forbidden", "the projected comment author does not match human_login")
    if exact_words not in comment.body:
        raise CommandError("invalid", "exact_words do not appear verbatim in the cited comment")


async def decide(
    uow: UnitOfWork, args: dict[str, Any], *, repository: str | None, human_login: str | None
) -> dict[str, Any]:
    who = _text(args["who"], "who")
    exact_words = _text(args["exact_words"], "exact_words")
    where_ref = args["where_ref"]
    governs = [_text(ticket, "governs entry") for ticket in args["governs"]]
    settles = [_text(ask_id, "settles entry") for ask_id in args["settles"]]
    await _verify_evidence(uow, who, exact_words, where_ref, repository, human_login)
    linked_asks: list[Ask] = []
    for ask_id in dict.fromkeys(settles):
        ask = await uow.find_ask(ask_id)
        if ask is None:
            raise CommandError("not_found", f"ask {ask_id!r} was not found")
        if ask.settled_by is not None:
            raise CommandError("conflict", f"ask {ask_id!r} has already been settled")
        linked_asks.append(ask)
    decision = Decision(
        f"dec_{uuid.uuid7()}",
        who,
        exact_words,
        where_ref,
        governs,
        settles,
        format_time(uow.now()),
    )
    await uow.insert_decision(decision)
    await settle_asks(uow, linked_asks, decision.decision_id)
    return {"decision_id": decision.decision_id}
