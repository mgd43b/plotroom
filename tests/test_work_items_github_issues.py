import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import pytest

from plotroom.adapters.work_items_github_issues import GitHubIssuesError, GitHubIssuesWorkItems
from plotroom.ports.work_items import WorkItem

ROOT = Path(__file__).resolve().parents[1]
STARTER = ROOT / ".claude/skills/bmad-agent-po/config/github-ticketing.toml"
READER = ROOT / "_bmad/method/scripts/tickets.py"


def issue(number: int, *labels: str, **fields: Any) -> dict[str, Any]:
    return {
        "resource": "issue",
        "number": number,
        "title": f"Ticket {number}",
        "labels": list(labels),
        "state": "open",
        "state_reason": None,
        "milestone": 1,
        "blocked_by": [],
        "sub_issues": [],
        **fields,
    }


def write_epic(
    initiative: Path,
    slug: str,
    tracker_id: int,
    entries: list[tuple[int, str, str, list[str]]],
    *,
    after: list[str] | None = None,
    refinement: dict[int, bool] | None = None,
    notes: dict[int, str] | None = None,
) -> None:
    refinement = refinement or {}
    notes = notes or {}
    epic = initiative / "tickets" / slug
    directory = epic / "tickets"
    directory.mkdir(parents=True)
    (epic / f"{slug}.md").write_text(
        f'---\ntype: epic\ntracker_id: "{tracker_id}"\nafter: {json.dumps(after or [])}\n---\n',
        encoding="utf-8",
    )
    (directory / "tickets.toml").write_text(
        "".join(
            f'[[entry]]\nid = {number}\ntype = "{kind}"\ntitle = "Ticket {number}"\n'
            f"after = {json.dumps(blockers)}\n"
            + ("refine = true\n" if number in refinement else "")
            for number, kind, _, blockers in entries
        ),
        encoding="utf-8",
    )
    for number, kind, state, blockers in entries:
        refined = f"refined: {json.dumps(refinement[number])}\n" if number in refinement else ""
        (directory / f"{kind}-{number}.md").write_text(
            f'---\nid: {number}\ntype: {kind}\ntracker_id: "{number}"\ntracker_status: {state}\n'
            f"after: {json.dumps(blockers)}\n{refined}---\n{notes.get(number, '')}",
            encoding="utf-8",
        )


async def test_work_items_match_the_synced_bmad_board(tmp_path: Path) -> None:
    config = tmp_path / "_bmad/custom/ticketing-store-config.toml"
    config.parent.mkdir(parents=True)
    config.write_text(
        '[tickets]\nstore = "github"\nrepo = "Owner/Repo"\n'
        '[tickets.types]\nstory = "work"\n[tickets.status]\nreview = "code-review"\n',
        encoding="utf-8",
    )
    initiative = tmp_path / "initiative"
    write_epic(
        initiative,
        "epic-primary",
        20,
        [
            (8, "story", "review", []),
            (6, "story", "backlog", ["8"]),
            (7, "story", "backlog", ["5"]),
            (5, "story", "dropped", []),
            (4, "spike", "backlog", ["10"]),
            (10, "story", "done", []),
            (12, "bug", "backlog", []),
            (13, "story", "backlog", []),
            (14, "spike", "backlog", []),
            (15, "story", "backlog", []),
            (16, "story", "backlog", []),
        ],
        refinement={12: True, 13: False, 14: False},
        notes={15: "## Notes\n- Unknown: pending research\n", 16: "<!--\nUnknown: example\n-->\n"},
    )
    write_epic(initiative, "epic-gated", 21, [(11, "story", "backlog", [])], after=["8"])
    (initiative / "tickets/tickets.toml").write_text(
        '[[epic]]\nid = 1\nslug = "epic-primary"\n[[epic]]\nid = 2\nslug = "epic-gated"\n',
        encoding="utf-8",
    )
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-I",
        "-B",
        str(READER),
        "--project-root",
        str(tmp_path),
        "next",
        str(initiative),
        "--synced",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    assert process.returncode == 0, (stdout, stderr)
    view = json.loads(stdout)
    expected = tuple(WorkItem(row["tracker_id"], row["title"]) for row in view["ready_to_start"])
    assert expected == (
        WorkItem("6", "Ticket 6"),
        WorkItem("4", "Ticket 4"),
        WorkItem("12", "Ticket 12"),
        WorkItem("16", "Ticket 16"),
    )

    review = issue(8, "work", "Code-Review")
    rows = (
        {"resource": "milestone", "number": 1, "state": "open"},
        issue(20, "epic", "in-progress", sub_issues=[8, 6, 7, 5, 4, 10, 12, 13, 14, 15, 16]),
        issue(21, "epic", "backlog", sub_issues=[11], blocked_by=[8]),
        issue(4, "spike", "backlog", blocked_by=[10]),
        issue(5, "work", state="closed", state_reason="not_planned"),
        issue(6, "work", "backlog", blocked_by=[8]),
        issue(7, "work", "backlog", blocked_by=[5]),
        review,
        issue(10, "work", state="closed", state_reason="completed"),
        issue(11, "work", "backlog"),
        issue(12, "bug", "backlog", body='```yaml\r\ntype: bug\r\nrefined: "true"\r\n```\r\n'),
        issue(13, "work", "backlog", body="```yaml\nrefined: false\n```\n"),
        issue(14, "spike", "backlog", body="```yaml\nrefined: false\n```\n"),
        issue(15, "work", "backlog", body="## Notes\n- Unknown: pending research\n"),
        issue(16, "work", "backlog", body="<!--\nUnknown: example\n-->\n"),
    )

    async def read() -> tuple[dict[str, Any], ...]:
        return rows

    source = GitHubIssuesWorkItems(read, STARTER, config, "owner/repo", milestone=1)
    assert await source.ready_items() == expected
    review.update(state="closed", state_reason="completed", labels=["work"])
    assert await source.ready_items() == (*expected, WorkItem("11", "Ticket 11"))


async def test_incomplete_or_ambiguous_work_has_no_eligible_leaf(tmp_path: Path) -> None:
    config = tmp_path / "ticketing-store-config.toml"
    config.write_text('[tickets]\nstore = "github"\n', encoding="utf-8")
    rows = (
        {"resource": "milestone", "number": 1, "state": "open"},
        {"resource": "milestone", "number": 2, "state": "closed"},
        issue(1, "story", "backlog", blocked_by=["other/repo#2"]),
        issue(2, "story", state="closed"),
        issue(3, "story", "backlog", blocked_by=[99]),
        issue(4, "story", "backlog", "review"),
        issue(5, "story", "bug", "backlog"),
        issue(6, "story", "backlog", milestone=2),
        issue(7, "bug", "backlog"),
        issue(8, "bug", "backlog", body="```yaml\nrefined: [true]\n```\n"),
        issue(9, "story", "backlog", body="```yaml\nrefined: true\n" + "\n" * 9000 + "```\n"),
    )

    async def read() -> tuple[dict[str, Any], ...]:
        return rows

    source = GitHubIssuesWorkItems(read, STARTER, config, "owner/repo")
    assert await source.ready_items() == ()
    rows += (issue(1, "story", "backlog"),)
    with pytest.raises(GitHubIssuesError, match="duplicate issues"):
        await source.ready_items()
