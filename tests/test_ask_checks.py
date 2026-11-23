from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from plotroom.core.ask_checks import (
    ASK_BURIED,
    ASK_OPTIONLESS,
    ASK_ORPHANED,
    ASK_STALE,
    Ask,
    IssueComment,
    ask_conditions,
)

POSTED = datetime(2026, 11, 15, 12, tzinfo=UTC)
ASK = Ask("ask_fixture", "fixture/repo", 1, "Choose?", ("A",), 42, POSTED.isoformat(), "")
LATER = POSTED + timedelta(seconds=1)


@pytest.mark.parametrize(
    ("comments", "state", "complete", "buried", "orphaned"),
    [
        ([], "open", True, False, False),
        ([], "closed", False, None, None),
        ([], "closed", True, False, True),
        ([IssueComment(43, "lane", LATER)], "open", False, True, False),
        ([IssueComment(43, "HUMAN", LATER)], "closed", False, False, None),
        ([IssueComment(43, None, LATER)], "closed", True, None, None),
        ([IssueComment(43, "human", POSTED)], "closed", True, None, None),
        (
            [IssueComment(43, "lane", LATER), IssueComment(44, "human", LATER)],
            "closed",
            True,
            False,
            False,
        ),
    ],
)
def test_ask_check_evidence(
    comments: list[IssueComment],
    state: str,
    complete: bool,
    buried: bool | None,
    orphaned: bool | None,
) -> None:
    assert ask_conditions(
        ASK,
        comments,
        issue_state=state,
        history_complete=complete,
        human_login="Human",
        stale_days=7,
        now=POSTED + timedelta(days=7),
    ) == {ASK_BURIED: buried, ASK_OPTIONLESS: False, ASK_STALE: False, ASK_ORPHANED: orphaned}


def test_optionless_and_stale_checks_clear_on_settlement() -> None:
    def conditions(ask: Ask) -> dict[str, bool | None]:
        return ask_conditions(
            ask,
            [],
            issue_state="closed",
            history_complete=True,
            human_login="Human",
            stale_days=7,
            now=POSTED + timedelta(days=7, microseconds=1),
        )

    ask = replace(ASK, options=())
    assert conditions(ask) == {
        ASK_BURIED: False,
        ASK_OPTIONLESS: True,
        ASK_STALE: True,
        ASK_ORPHANED: True,
    }
    assert not any(conditions(replace(ask, settled_by="dec_fixture")).values())
