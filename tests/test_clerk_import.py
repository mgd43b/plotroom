"""The local clerk import preserves template facts and uses the normal board lifecycle."""

from collections.abc import AsyncIterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from conftest import write_config
from fakes import FixedClock
from plotroom.adapters.mcp.proxy import LocalEndpoint
from plotroom.adapters.store_sqlite import SqliteStore
from plotroom.app import clerk_import as clerk_cli
from plotroom.app import daemon_start
from plotroom.app.clerk_import import parse_ledger, parse_triggers
from plotroom.app.config import load_config
from plotroom.core import clerk_import
from plotroom.core import lanes as lane_commands
from plotroom.core.catalog import build_catalog
from plotroom.core.events import LedgerError
from plotroom.core.github import PULL_REQUESTS, ObservedPull, apply_pull_poll
from plotroom.core.ledger import Ledger
from plotroom.core.trigger_actions import DeliveryRoute
from plotroom.core.triggers import TRIGGER_FIRED
from plotroom.core.unit_of_work import UnitOfWork

REPO = "fixture/project"
FIXTURES = Path(__file__).parent / "fixtures" / "clerk_import"
STAMP = "2026-11-23T12:01:00Z"
PULLS = (
    ObservedPull(41, "fixture/projected", "a" * 40, REPO, "open", STAMP, ""),
    ObservedPull(42, "fixture/peer", "b" * 40, REPO, "open", STAMP, ""),
    ObservedPull(43, "fixture/absent", "c" * 40, REPO, "open", STAMP, ""),
)


def inputs() -> dict[str, Any]:
    repository, pulls, skips = parse_ledger((FIXTURES / "ledger.md").read_text())
    triggers = parse_triggers((FIXTURES / "triggers.md").read_text())
    return {"ledger_repository": repository, "pulls": pulls, "skips": skips, "triggers": triggers}


async def observe(ledger: Ledger, pulls: tuple[ObservedPull, ...]) -> None:
    cursor = await ledger.fetch_cursor(REPO, PULL_REQUESTS)
    assert await ledger.run(
        "system", lambda uow: apply_pull_poll(uow, REPO, cursor, pulls, "2026-11-23T12:02:00Z")
    )


@pytest.fixture
async def board(tmp_path: Path) -> AsyncIterator[tuple[Ledger, SqliteStore]]:
    store = SqliteStore("demo", tmp_path)
    await store.migrate()
    catalog = build_catalog(
        frozenset({"fixture", "peer"}),
        github_repository=REPO,
        delivery_routes={"fixture": DeliveryRoute("switchboard", "fixture-worker")},
    )
    ledger = Ledger(store, catalog, FixedClock(), github_repository=REPO)
    ledger.start()
    try:
        await observe(ledger, PULLS)
        yield ledger, store
    finally:
        await ledger.stop()
        await store.close()


def test_recognizes_exact_forms_and_preserves_unrecognized_prose() -> None:
    args = inputs()
    exact, fired, bind = args["triggers"]
    assert (exact["predicate"], exact["channel"], exact["notify"], exact["fired"]) == (
        {"pr_merged": {"number": 41}},
        "relay:fixture-worker",
        True,
        False,
    )
    assert fired["predicate"] == {"pr_merged": {"branch_glob": "fixture/completed*"}}
    assert fired["fired"]
    assert bind["predicate"] is None
    assert "repairs the fixture" in bind["source"]
    assert [row["pr"] for row in args["pulls"]] == [41, 42, 43]
    assert args["pulls"][1]["after_merge"] == "Verify artifact | runbook"

    text = """# Clerk triggers
```markdown
## T99: #99 merges
- Tell the coordinator: "A quoted example."
```
## T7: #7 merges
- Tell the coordinator: "This says FIRED 2026-11-23 12:00Z abcdef1."
## T8: #8 merges
- Tell the coordinator: "A canceled instruction."
- CANCELED
## T9: #9 merges
- Tell the coordinator: "Has an undocumented step."
- Execute a command.
"""
    recognized = parse_triggers(text)
    assert [row["label"] for row in recognized] == ["T7", "T8", "T9"]
    assert recognized[0]["predicate"]
    assert not recognized[0]["fired"]
    assert [row["predicate"] for row in recognized[1:]] == [None, None]


def test_refuses_rows_that_do_not_match_the_template() -> None:
    text = (FIXTURES / "ledger.md").read_text()
    for broken in (
        text.replace("Owner (lane)", "Unofficial owner"),
        text.replace("#42", "#invalid"),
    ):
        with pytest.raises(ValueError, match=r"column|number"):
            parse_ledger(broken)
    with pytest.raises(ValueError, match="conflicting entries"):
        parse_triggers("## T1: #10 merges\n## T1: #11 merges")


OPEN_TABLE = (
    "## Open PRs\n"
    "| PR | Owner (lane) | Kind | Channel | State | After merge |\n"
    "|---|---|---|---|---|---|\n"
)
OPEN_TABLE_WITH_CLOSING_HASHES = OPEN_TABLE.replace("Open PRs", "Open PRs ##")
EMPTY_OPEN_ROW = "| NONE | | | | | |\n"
NUMBERED_OPEN_ROW = "| #41 | fixture | codex | coordinator-only | waiting | Review output |\n"


async def test_empty_open_pr_marker_imports_and_replays(
    board: tuple[Ledger, SqliteStore],
) -> None:
    ledger, store = board
    repo, pulls, skips = parse_ledger(
        f"# Clerk ledger: {REPO}\n\n{OPEN_TABLE}{EMPTY_OPEN_ROW}\n"
        "## Skip list\n| PR | Why, and who asked |\n|---|---|\n| #48 | Fixture hold |\n"
    )
    assert pulls == []
    args = {"ledger_repository": repo, "pulls": pulls, "skips": skips, "triggers": []}
    first = await ledger.execute("orchestrator", "clerk_import", "empty-board", args)
    assert first.result["lanes"] == {}
    assert first.result["triggers"] == {}
    assert set(first.result["items"]) == {"skip:48"}
    cursor = await store.head_seq()
    before = await ledger.events_after(0)
    assert await store.pending_action() is None
    repeat = await ledger.execute("orchestrator", "clerk_import", "empty-board", args)
    assert repeat.replayed
    assert repeat.result == first.result
    assert await store.head_seq() == cursor
    assert await ledger.events_after(0) == before
    assert await store.pending_action() is None


@pytest.mark.parametrize(
    "body",
    [
        "| NONE | fixture | | | | |\n",
        "| NONE | | | | |\n",
        "| (NONE) | | | | | |\n",
        EMPTY_OPEN_ROW + EMPTY_OPEN_ROW,
        EMPTY_OPEN_ROW + "| (soon) | | | | | |\n",
        EMPTY_OPEN_ROW + NUMBERED_OPEN_ROW,
        NUMBERED_OPEN_ROW + EMPTY_OPEN_ROW,
        EMPTY_OPEN_ROW + "\n" + OPEN_TABLE + NUMBERED_OPEN_ROW,
        NUMBERED_OPEN_ROW + "\n" + OPEN_TABLE + EMPTY_OPEN_ROW,
        EMPTY_OPEN_ROW + "\n" + OPEN_TABLE_WITH_CLOSING_HASHES + NUMBERED_OPEN_ROW,
        NUMBERED_OPEN_ROW + "\n" + OPEN_TABLE_WITH_CLOSING_HASHES + EMPTY_OPEN_ROW,
    ],
)
def test_refuses_ambiguous_empty_open_pr_marker(body: str) -> None:
    with pytest.raises(ValueError, match=r"empty Open PRs marker|row width|positive #PR"):
        parse_ledger(f"# Clerk ledger: {REPO}\n\n{OPEN_TABLE}{body}")


async def test_import_states_lane_links_checklist_items_and_replay(
    board: tuple[Ledger, SqliteStore],
) -> None:
    ledger, store = board
    arguments = inputs()
    first = await ledger.execute("orchestrator", "clerk_import", "snapshot", arguments)
    data = first.result
    assert set(data["triggers"]) == {"T1", "T2"}
    assert set(data["lanes"]) == {"41", "42"}
    assert set(data["items"]) == {"trigger:T3", "pull:43", "skip:44", "skip:45"}
    active = await ledger.list_triggers("orchestrator", None)
    by_id = {row["id"]: row for row in active}
    opened = by_id[data["triggers"]["T1"]]
    historical = by_id[data["triggers"]["T2"]]
    assert (opened["state"], historical["state"]) == ("open", "fired")
    assert "[sha8]" in opened["import_note"]
    assert "[N]" in opened["import_note"]
    assert historical["fired_seq"] is not None
    assert historical["action_key"] is None
    assert not [event for event in await ledger.events_after(0) if event.kind == TRIGGER_FIRED]
    assert await store.pending_action() is None
    list_board, *_ = await ledger.snapshot_summary(
        "orchestrator", checklist_lane=data["lanes"]["42"]
    )
    lanes = {lane.id: lane.render() for lane in list_board[3]}
    linked = lanes[data["lanes"]["41"]]
    checklisted = lanes[data["lanes"]["42"]]
    assert (linked["branch"], linked["imported_channel"], linked["imported_trigger_id"]) == (
        PULLS[0].branch,
        "relay:fixture-worker",
        opened["id"],
    )
    assert checklisted["branch"] == PULLS[1].branch
    assert checklisted["imported_channel"] == "coordinator-only"
    assert checklisted["checklist"]["steps"][0]["step"] == "Verify artifact | runbook"

    await ledger.execute("orchestrator", "ack", "ack-bind", {"item": data["items"]["trigger:T3"]})
    before = await ledger.events_after(0)
    repeat = await ledger.execute("orchestrator", "clerk_import", "snapshot", arguments)
    assert repeat.replayed
    assert repeat.result == data
    assert await ledger.events_after(0) == before
    _, current = await store.read_next("orchestrator", 0, 50)
    assert data["items"]["trigger:T3"] not in {item.id for item in current}
    overlapping = await ledger.execute(
        "orchestrator",
        "clerk_import",
        "overlapping",
        {"triggers": [arguments["triggers"][2]], "pulls": [], "skips": []},
    )
    assert overlapping.result["items"] == {"trigger:T3": data["items"]["trigger:T3"]}
    _, current = await store.read_next("orchestrator", 0, 50)
    assert data["items"]["trigger:T3"] not in {item.id for item in current}
    assert await ledger.list_triggers("orchestrator", None) == active
    assert await store.pending_action() is None

    # A separately requested ledger import can reuse triggers already registered in this project.
    another = await ledger.execute(
        "orchestrator", "clerk_import", "ledger-only", {**arguments, "triggers": []}
    )
    assert another.result["lanes"] == data["lanes"]
    assert another.result["items"] == {
        name: item for name, item in data["items"].items() if name != "trigger:T3"
    }

    merged = replace(PULLS[0], state="merged", updated_at="2026-11-23T12:01:30Z")
    await observe(ledger, (merged, *PULLS[1:]))
    await observe(ledger, (merged, *PULLS[1:]))
    [fired] = [event for event in await ledger.events_after(0) if event.kind == TRIGGER_FIRED]
    assert fired.fields["trigger_id"] == opened["id"]
    post = next(
        row for row in await ledger.list_triggers("orchestrator", None) if row["id"] == opened["id"]
    )
    assert post["action"]["send"]["text"] == "Fixture #[N] at [sha8]. Literal $(fixture-only)."
    assert post["state"] == "fired"
    actions = [row.action for row in await store.pending_actions(after=0, limit=10)]
    message = post["action"]["send"]["text"]
    assert len(actions) == 2
    assert {action.cause_id for action in actions} == {fired.id}
    assert {action.kind: action.payload for action in actions} == {
        "send": {"worker": "fixture", "text": message},
        "notify_orchestrator": {"text": message},
    }


async def test_import_commands_are_orchestrator_only(
    board: tuple[Ledger, SqliteStore],
) -> None:
    ledger, store = board
    operations: tuple[tuple[str, dict[str, Any]], ...] = (
        ("clerk_import", inputs()),
        ("checklist_import", {"lane": "any", "rows": []}),
        (
            "trigger",
            {
                "predicate": {"pr_merged": {"number": 41}},
                "action": {"notify_orchestrator": {"text": "fixture"}},
                "imported_fired": True,
            },
        ),
    )
    for role in ("clerk", "worker:fixture", "bridge", "drain"):
        for command, args in operations:
            with pytest.raises(LedgerError, match="may not execute"):
                await ledger.execute(role, command, "refused", args)
    assert await ledger.list_triggers("orchestrator", None) == []
    assert await store.pending_action() is None


def test_does_not_activate_markdown_examples_or_unrecognized_qualifiers() -> None:
    assert (
        parse_triggers(
            "# Clerk triggers\n<!--\n## T3: #41 merges\n"
            '- Tell the coordinator: "Retired."\n## Notes\n-->\n'
        )
        == []
    )
    text = (FIXTURES / "ledger.md").read_text()
    with pytest.raises(ValueError, match="unexpected Markdown table"):
        parse_ledger(text.replace("## Open PRs\n", "## Open PRs\n```markdown\n"))
    [qualified] = parse_triggers(
        '## T1: #41 merges (unless #42 is open)\n- Tell the coordinator: "Wait."\n'
    )
    assert qualified["predicate"] is None
    [quoted] = parse_triggers(
        '## T1: #41 merges\n- Tell the coordinator: "Quote `<!-- fixture -->` literally."\n'
    )
    assert quoted["text"] == "Quote `<!-- fixture -->` literally."
    assert quoted["predicate"] == {"pr_merged": {"number": 41}}
    indented = "\n".join(
        f"    {line}" if line.startswith("|") else line for line in text.splitlines()
    )
    with pytest.raises(ValueError, match="unexpected Markdown table"):
        parse_ledger(indented)


async def test_ledger_repository_mismatch_leaves_no_partial_import(
    board: tuple[Ledger, SqliteStore],
) -> None:
    ledger, store = board
    before = await ledger.events_after(0)
    with pytest.raises(LedgerError, match="repository does not match"):
        await ledger.execute(
            "orchestrator",
            "clerk_import",
            "wrong-repository",
            {**inputs(), "ledger_repository": "fixture/other"},
        )
    assert await ledger.events_after(0) == before
    assert await ledger.list_triggers("orchestrator", None) == []
    assert await store.pending_action() is None


async def test_conflicting_trigger_label_does_not_link_a_historical_trigger(
    board: tuple[Ledger, SqliteStore],
) -> None:
    ledger, _ = board
    first = await ledger.execute(
        "orchestrator",
        "clerk_import",
        "triggers-only",
        {"triggers": inputs()["triggers"], "pulls": [], "skips": []},
    )
    [conflict] = parse_triggers(
        '## T1: #41 merges\n- Tell the coordinator: "Different exact message."\n'
    )
    second = await ledger.execute(
        "orchestrator",
        "clerk_import",
        "conflicting-label",
        {**inputs(), "triggers": [conflict]},
    )
    assert "trigger:T1" in second.result["items"]
    lane_id = second.result["lanes"]["41"]
    snapshot, *_ = await ledger.snapshot_summary("orchestrator", checklist_lane=lane_id)
    lane = next(lane for lane in snapshot[3] if lane.id == lane_id)
    assert lane.imported_trigger_id is None
    assert lane.checklist is not None
    assert [step.step for step in lane.checklist.steps] == ["Trigger T1"]
    old = await ledger.list_triggers("orchestrator", None)
    assert {row["id"] for row in old} == set(first.result["triggers"].values())


async def test_updated_hold_context_reuses_the_single_pr_item(
    board: tuple[Ledger, SqliteStore],
) -> None:
    ledger, store = board
    original: dict[str, Any] = {**inputs(), "triggers": [], "pulls": []}
    first = await ledger.execute("orchestrator", "clerk_import", "holds", original)
    item_id = first.result["items"]["skip:44"]
    await ledger.execute("orchestrator", "ack", "seen-hold", {"item": item_id})
    updated = {
        **original,
        "skips": [{"pr": 44, "reason": "Owner updated the fixture hold"}],
    }
    second = await ledger.execute("orchestrator", "clerk_import", "revised-hold", updated)
    assert second.result["items"]["skip:44"] == item_id
    _, current = await store.read_next("orchestrator", 0, 50)
    [hold] = [item for item in current if item.id == item_id]
    assert hold.context["reason"] == "Owner updated the fixture hold"
    assert hold.state == "open"

    overlapping = {
        **original,
        "skips": [original["skips"][0], {"pr": 46, "reason": "Another fixture hold"}],
    }
    older_row = await ledger.execute(
        "orchestrator", "clerk_import", "old-hold-in-batch", overlapping
    )
    assert older_row.result["items"]["skip:44"] == item_id
    _, current = await store.read_next("orchestrator", 0, 50)
    [hold] = [item for item in current if item.id == item_id]
    assert hold.context["reason"] == "Owner updated the fixture hold"


async def test_a_prior_bind_also_reserves_the_trigger_label(
    board: tuple[Ledger, SqliteStore],
) -> None:
    ledger, _ = board
    [prose] = parse_triggers(
        '## T1: the fixture work merges\n- Tell the coordinator: "Old handoff."\n'
    )
    empty: dict[str, Any] = {"pulls": [], "skips": []}
    first = await ledger.execute(
        "orchestrator", "clerk_import", "bind-first", {**empty, "triggers": [prose]}
    )
    assert "trigger:T1" in first.result["items"]
    second = await ledger.execute(
        "orchestrator",
        "clerk_import",
        "same-label",
        {**empty, "triggers": [inputs()["triggers"][0]]},
    )
    assert "trigger:T1" in second.result["items"]
    assert second.result["triggers"] == {}

    later = await ledger.execute(
        "orchestrator", "clerk_import", "ledger-after-binding", {**inputs(), "triggers": []}
    )
    lane_id = later.result["lanes"]["41"]
    snapshot, *_ = await ledger.snapshot_summary("orchestrator", checklist_lane=lane_id)
    lane = next(lane for lane in snapshot[3] if lane.id == lane_id)
    assert lane.imported_trigger_id is None
    assert lane.checklist is not None
    assert [step.step for step in lane.checklist.steps] == ["Trigger T1"]


@pytest.mark.parametrize(
    "held_state", ["Holding for coordinator review", "On hold — awaiting review"]
)
async def test_open_pr_state_holds_share_the_skip_advisory(
    board: tuple[Ledger, SqliteStore], held_state: str
) -> None:
    ledger, store = board
    text = (FIXTURES / "ledger.md").read_text()
    repo, initial_pulls, _ = parse_ledger(text)
    initial = await ledger.execute(
        "orchestrator",
        "clerk_import",
        "before-holds",
        {"ledger_repository": repo, "triggers": [], "pulls": initial_pulls, "skips": []},
    )
    text = text.replace(
        "`relay:fixture-worker` | waiting",
        f"`relay:fixture-worker` | {held_state}",
    ).replace("`coordinator-only` | waiting", "`coordinator-only` | Hold until the next window")
    repo, pulls, _ = parse_ledger(text)
    assert pulls[0]["state"] == held_state
    data = await ledger.execute(
        "orchestrator",
        "clerk_import",
        "state-holds",
        {
            "ledger_repository": repo,
            "triggers": [],
            "pulls": pulls,
            "skips": [{"pr": 42, "reason": "Explicit fixture skip"}],
        },
    )
    _, current = await store.read_next("orchestrator", 0, 50)
    holds = {
        item.id: item.context["reason"] for item in current if item.kind.endswith("skipped_pull")
    }
    assert holds == {
        data.result["items"]["skip:41"]: held_state,
        data.result["items"]["skip:42"]: "Explicit fixture skip",
    }
    assert data.result["lanes"] == initial.result["lanes"]
    assert set(data.result["items"]) == {"pull:43", "skip:41", "skip:42"}


async def test_trigger_linked_lane_tracks_the_projected_head(
    board: tuple[Ledger, SqliteStore],
) -> None:
    ledger, _ = board
    imported = await ledger.execute("orchestrator", "clerk_import", "projected-head", inputs())
    lane_id = imported.result["lanes"]["41"]
    lanes = (await ledger.snapshot_board("orchestrator"))[0][3]
    lane = next(lane for lane in lanes if lane.id == lane_id)
    assert (lane.pr, lane.head_sha) == (41, PULLS[0].head_sha)
    assert lane.imported_trigger_id == imported.result["triggers"]["T1"]

    pushed = replace(PULLS[0], head_sha="f" * 40, updated_at="2026-11-23T12:01:30Z")
    await observe(ledger, (pushed, *PULLS[1:]))
    lanes = (await ledger.snapshot_board("orchestrator"))[0][3]
    lane = next(lane for lane in lanes if lane.id == lane_id)
    assert (lane.pr, lane.head_sha) == (41, pushed.head_sha)
    assert lane.imported_trigger_id == imported.result["triggers"]["T1"]


async def test_import_refreshes_idle_workers_once(
    board: tuple[Ledger, SqliteStore], monkeypatch: pytest.MonkeyPatch
) -> None:
    ledger, _ = board
    refresh = lane_commands.refresh_idle_workers
    calls: list[frozenset[str]] = []

    async def observed(uow: UnitOfWork, workers: frozenset[str]) -> None:
        calls.append(workers)
        await refresh(uow, workers)

    monkeypatch.setattr(clerk_import, "refresh_idle_workers", observed)
    monkeypatch.setattr(lane_commands, "refresh_idle_workers", observed)
    result = await ledger.execute("orchestrator", "clerk_import", "batch", inputs())
    assert len(result.result["lanes"]) == 2
    assert calls == [frozenset({"fixture", "peer"})]
    await ledger.execute("orchestrator", "clerk_import", "same-rows", inputs())
    assert calls == [frozenset({"fixture", "peer"})]


def test_backtick_code_span_does_not_open_a_fence() -> None:
    [record] = parse_triggers(
        "# Clerk triggers\n\n```example```\n"
        '## T1: #41 merges\n- Tell the coordinator: "Live fixture."\n'
    )
    assert record["predicate"] == {"pr_merged": {"number": 41}}


@pytest.mark.parametrize("suffix", ["\u00a0", "\u0085", "\u2028"])
def test_non_ascii_space_does_not_close_a_fence(suffix: str) -> None:
    assert (
        parse_triggers(
            f"# Clerk triggers\n\n```markdown\n```{suffix}\n"
            '## T1: #41 merges\n- Tell the coordinator: "Hidden example."\n'
        )
        == []
    )


def test_cli_refuses_a_ledger_in_the_triggers_slot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def unexpected(*_args: object) -> int:
        pytest.fail("a misfiled ledger must not be sent to the daemon")

    monkeypatch.setattr(clerk_cli, "_submit", unexpected)
    assert clerk_cli.run_import(None, None, FIXTURES / "ledger.md", None) == 2
    indented = tmp_path / "indented-triggers.md"
    indented.write_text("    " + (FIXTURES / "triggers.md").read_text(), encoding="utf-8")
    assert clerk_cli.run_import(None, None, indented, None) == 2
    with pytest.raises(ValueError, match="owner/repository title"):
        parse_ledger("    " + (FIXTURES / "ledger.md").read_text())


async def test_existing_local_probe_can_follow_a_publication_handoff(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    location = daemon_start.DaemonLocation(load_config(write_config(tmp_path), {}))
    old = LocalEndpoint("http://127.0.0.1:41/mcp", "127.0.0.1:41", "/fixture/old", 41)
    new = LocalEndpoint("http://127.0.0.1:42/mcp", "127.0.0.1:42", "/fixture/new", 42)
    published = iter((old, new, new, new))
    probed: list[LocalEndpoint] = []

    async def current(_location: daemon_start.DaemonLocation) -> LocalEndpoint | None:
        return next(published)

    async def probe(target: LocalEndpoint, *, within: float) -> int:
        assert within > 0
        probed.append(target)
        return 401

    monkeypatch.setattr(daemon_start.DaemonLocation, "current_endpoint", current)
    monkeypatch.setattr(daemon_start, "probe_status", probe)
    assert await daemon_start.require_daemon(location, within=10) == new
    assert probed == [old, new]


async def test_import_preserves_the_gate_binding_of_an_existing_pattern_lane(
    board: tuple[Ledger, SqliteStore],
) -> None:
    ledger, store = board
    await ledger.execute(
        "orchestrator",
        "lane_start",
        "pattern",
        {
            "lane": "existing",
            "worker": "peer",
            "branch": "legacy",
            "branch_pattern": "fixture/project*",
            "work_items": [],
        },
    )
    args = inputs()
    result = await ledger.execute(
        "orchestrator",
        "clerk_import",
        "pattern-conflict",
        {**args, "pulls": args["pulls"][:1], "triggers": [], "skips": []},
    )
    assert result.result["lanes"] == {}
    _, items = await store.read_next("orchestrator", 0, 50)
    [review] = [item for item in items if item.id == result.result["items"]["pull:41"]]
    assert review.context["reason"] == "The worker, branch, or PR already has a lane"
    snapshot = await store.read_merge_gate(REPO, 41)
    assert snapshot.lane is not None
    assert snapshot.lane.id == "existing"
    assert not snapshot.ambiguous_lane


async def test_checklist_import_preserves_an_outstanding_wait(
    board: tuple[Ledger, SqliteStore],
) -> None:
    ledger, store = board
    await ledger.execute(
        "orchestrator",
        "lane_start",
        "start",
        {"lane": "existing", "worker": "fixture", "branch": PULLS[0].branch, "work_items": []},
    )
    await ledger.execute(
        "worker:fixture", "lane_update", "wait", {"lane": "existing", "waiting_on": "orchestrator"}
    )
    _, before = await store.read_next("orchestrator", 0, 50)
    [waiting] = [item for item in before if item.kind == "lane_waiting"]
    imported = await ledger.execute(
        "orchestrator",
        "checklist_import",
        "rows",
        {
            "lane": "existing",
            "rows": [{"step": "Check receipt", "owner": "fixture", "ref": "local"}],
        },
    )
    assert (imported.result["pr"], imported.result["state"]) == (41, "pending")
    board_lanes = (await ledger.snapshot_board("orchestrator"))[0][3]
    assert next(lane for lane in board_lanes if lane.id == "existing").waiting_on == "orchestrator"
    _, after = await store.read_next("orchestrator", 0, 50)
    assert waiting in after
