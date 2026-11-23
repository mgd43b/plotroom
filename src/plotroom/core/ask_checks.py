"""Pure conditions for the orchestrator's GitHub ask queue."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from plotroom.core.next_items import ItemKind

ASK_BURIED = "ask_buried"
ASK_OPTIONLESS = "ask_optionless"
ASK_STALE = "ask_stale"
ASK_ORPHANED = "ask_orphaned"
DEFAULT_ASK_STALE_DAYS = 7
ASK_ITEMS = (
    ItemKind(ASK_BURIED, 20, True, ("Review the ask and the latest issue comments",)),
    ItemKind(ASK_OPTIONLESS, 20, True, ("Record the options for the ask",)),
    ItemKind(ASK_STALE, 20, True, ("Review the outstanding ask",)),
    ItemKind(ASK_ORPHANED, 20, True, ("Review the ask on the closed issue",)),
)


@dataclass(frozen=True)
class Ask:
    ask_id: str
    repository: str
    issue: int
    text: str
    options: tuple[str, ...]
    comment_id: int
    comment_created_at: str
    created_at: str
    settled_by: str | None = None

    def render(self) -> dict[str, Any]:
        return {
            "ask_id": self.ask_id,
            "repository": self.repository,
            "issue": self.issue,
            "text": self.text,
            "options": list(self.options),
            "comment_id": self.comment_id,
            "comment_created_at": self.comment_created_at,
            "created_at": self.created_at,
            "settled_by": self.settled_by,
        }


@dataclass(frozen=True)
class IssueComment:
    id: int
    author: str | None
    created_at: datetime | None


def comment_time(value: str) -> datetime | None:
    try:
        time = datetime.fromisoformat(value)
    except ValueError:
        return None
    return time if time.utcoffset() is not None else None


def ask_conditions(
    ask: Ask,
    comments: list[IssueComment],
    *,
    issue_state: str | None,
    history_complete: bool,
    human_login: str | None,
    stale_days: int,
    now: datetime,
) -> dict[str, bool | None]:
    """Unknown conditions preserve any existing item while evidence catches up."""
    if ask.settled_by is not None:
        return dict.fromkeys((kind.name for kind in ASK_ITEMS), False)

    posted = datetime.fromisoformat(ask.comment_created_at)
    known_human = human_login.casefold() if human_login else None
    later: list[tuple[datetime, str | None]] = []
    uncertain = False
    for comment in comments:
        if comment.id == ask.comment_id:
            continue
        author = comment.author.casefold() if comment.author else None
        moment = comment.created_at
        if moment is not None and moment > posted:
            later.append((moment, author))
        elif (moment is None or moment == posted) and author in (known_human, None):
            uncertain = True

    buried: bool | None = None
    replied = known_human is not None and any(author == known_human for _, author in later)
    if known_human is not None:
        latest = max((moment for moment, _ in later), default=None)
        latest_authors = {author for moment, author in later if moment == latest}
        if known_human in latest_authors:
            buried = False
        elif latest_authors and None not in latest_authors and not uncertain:
            buried = True
        elif not latest_authors and history_complete and not uncertain:
            buried = False

    orphaned: bool | None = None
    if issue_state == "open" or (history_complete and replied):
        orphaned = False
    elif (
        issue_state == "closed"
        and history_complete
        and known_human is not None
        and not uncertain
        and all(author is not None for _, author in later)
    ):
        orphaned = True

    age = now - posted
    stale = age.days > stale_days or (
        age.days == stale_days and (age.seconds > 0 or age.microseconds > 0)
    )
    return {
        ASK_BURIED: buried,
        ASK_OPTIONLESS: not ask.options,
        ASK_STALE: stale,
        ASK_ORPHANED: orphaned,
    }
