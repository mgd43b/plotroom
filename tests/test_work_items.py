import asyncio
import shutil
from pathlib import Path

import pytest

from conftest import BRIEF_TICKET_BODY, BRIEF_TICKET_REF, make_brief_ticket
from fakes import FixedClock
from plotroom.adapters import work_items_bmad
from plotroom.adapters.store_sqlite import SqliteStore
from plotroom.adapters.work_items_bmad import BmadTicketTree, TicketTreeError
from plotroom.app.runtime import ProjectRuntime
from plotroom.core.catalog import build_catalog
from plotroom.core.events import LedgerError
from plotroom.core.ledger import Ledger
from plotroom.ports.work_items import MAX_WORK_ITEM_BODY_BYTES, WorkItem


async def test_local_body_preserves_text_and_bounds_ticket_files(tmp_path: Path) -> None:
    tree, installation = make_brief_ticket(tmp_path)
    source = BmadTicketTree(tree, installation)
    assert await source.body(BRIEF_TICKET_REF) == BRIEF_TICKET_BODY
    assert await source.body("unknown reference") is None
    assert await source.body("--help") is None

    ticket = tree / "tickets" / "story-fixture-ticket.md"
    alternating = BRIEF_TICKET_BODY.replace("\n", "\r\n")
    ticket.write_bytes(alternating.encode())
    assert await source.body(BRIEF_TICKET_REF) == alternating
    with ticket.open("ab") as file:
        file.write(b"a" * MAX_WORK_ITEM_BODY_BYTES)
    with pytest.raises(TicketTreeError, match="size limit"):
        await source.body(BRIEF_TICKET_REF)

    outside = tmp_path / "outside.md"
    outside.write_bytes(BRIEF_TICKET_BODY.encode())
    ticket.unlink()
    ticket.symlink_to(outside)
    with pytest.raises(TicketTreeError, match="configured tree"):
        await source.body(BRIEF_TICKET_REF)


async def test_compact_body_requires_a_bounded_regular_breakdown(tmp_path: Path) -> None:
    tree, installation = make_brief_ticket(tmp_path)
    source = BmadTicketTree(tree, installation)
    breakdown = tree / "tickets" / "tickets.toml"
    compact = '[[entry]]\nid = 2\ntype = "story"\ntitle = "Compact"\n'
    breakdown.write_text(compact + "#" * MAX_WORK_ITEM_BODY_BYTES, encoding="utf-8")
    with pytest.raises(TicketTreeError, match="size limit"):
        await source.body("2")

    outside = tmp_path / "outside.toml"
    outside.write_text(compact, encoding="utf-8")
    breakdown.unlink()
    breakdown.symlink_to(outside)
    with pytest.raises(TicketTreeError, match="configured tree"):
        await source.body("2")


@pytest.mark.parametrize(
    ("ref", "original_id", "new_id"), [("2", "2", '"002"'), ("2a", '"2a"', '"2a"')]
)
async def test_compact_body_uses_confined_breakdown_contents(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, ref: str, original_id: str, new_id: str
) -> None:
    tree, installation = make_brief_ticket(tmp_path)
    breakdown = tree / "tickets" / "tickets.toml"
    breakdown.write_text(
        f'[[entry]]\nid = {original_id}\ntype = "story"\ntitle = "Stale subprocess text"\n',
        encoding="utf-8",
    )
    replacement = breakdown.with_suffix(".new")
    replacement.write_bytes(
        (
            f'[[entry]]\nid = {new_id}\ntype = "story"\ntitle = "Current file text"\r\n'
            'description = """first\r\nsecond"""\r\nverify = 7\r\n'
            'references = ["fixture.md", 23]\r\nnotes = ["Check the file.", true]\r\n'
        ).encode("utf-8-sig")
    )
    read_source_file = work_items_bmad._read_source_file  # pyright: ignore[reportPrivateUsage]

    def read_after_replacement(scope: Path, path: Path) -> str:
        assert path == breakdown
        replacement.replace(path)
        return read_source_file(scope, path)

    monkeypatch.setattr(work_items_bmad, "_read_source_file", read_after_replacement)
    assert await BmadTicketTree(tree, installation).body(ref) == (
        "# Current file text\n\n## Description\n\nfirst\nsecond\n\n"
        "## Requirements\n\nVerify: 7\n\n## References\n\n- fixture.md\n- 23\n\n"
        "## Notes\n\n- Check the file.\n- True\n"
    )


async def test_local_bmad_mirror_follows_plans_and_closes_idle_item(tmp_path: Path) -> None:
    reader = Path("_bmad/method/scripts/tickets.py")
    installation = tmp_path / "trusted"
    installed = installation / reader
    installed.parent.mkdir(parents=True)
    shutil.copyfile(reader, installed)
    source = tmp_path / "mirror"
    (source / "_bmad").mkdir(parents=True)
    tree = source / "epic-fixture"
    tickets = tree / "tickets"
    tickets.mkdir(parents=True)
    (tree / "epic-fixture.md").write_text("---\ntype: epic\n---\n", encoding="utf-8")
    (tickets / "tickets.toml").write_text(
        """[[entry]]
id = 1
type = "story"
title = "Ready"
description = "Keep {worker} literal.\\nPreserve the next line."
verify = "Read the ready reference."
references = ["fixture/one.md", "fixture/two.md"]
notes = ["First note.", "Second note."]
[[entry]]
id = 2
type = "story"
title = "Blocked by plan"
after = [1]
[[entry]]
id = 3
type = "story"
title = "In progress"
""",
        encoding="utf-8",
    )
    (tickets / "story-in-progress-plan.md").write_text(
        "---\ntype: feature\nticket: 3\nstatus: in-progress\n---\n", encoding="utf-8"
    )
    source_reader = source / reader
    source_reader.parent.mkdir(parents=True)
    source_reader.write_text("raise AssertionError('the mirror supplied code')", encoding="utf-8")
    adapter = BmadTicketTree(tree, installation)
    with pytest.raises(TicketTreeError, match="outside the mirrored checkout"):
        await BmadTicketTree(tree, source).ready_items()
    config_dir = source / "_bmad" / "custom"
    config_dir.mkdir()
    store_config = config_dir / "ticketing-store-config.toml"
    store_config.write_text('[tickets]\nstore = "github"\n', encoding="utf-8")
    with pytest.raises(TicketTreeError, match="repo store"):
        await adapter.ready_items()
    store_config.write_text('[tickets]\nstore = "repo"\n', encoding="utf-8")
    original = {file: file.read_bytes() for file in tree.rglob("*") if file.is_file()}
    store = SqliteStore("demo", tmp_path)
    await store.migrate()
    clock = FixedClock()
    workers = frozenset({"fixture"})
    ledger = Ledger(store, build_catalog(workers | {"other"}), clock)
    ledger.start()
    runtime = ProjectRuntime(
        ledger,
        clock,
        tick_seconds=5,
        action_attempts=3,
        workers=workers,
        work_items=adapter,
    )
    try:
        clock.advance(5)
        assert await runtime.tick_if_due()
        mirror = await ledger.ready_work_items()
        assert len(mirror) == 1
        assert mirror[0] == WorkItem(mirror[0].ref, "Ready")
        brief = await ledger.brief("orchestrator", mirror[0].ref, "fixture", adapter)
        assert brief.startswith(
            "# Lane brief for fixture\n\n## Work item 1\n\n"
            "# Ready\n\n## Description\n\nKeep {worker} literal.\nPreserve the next line.\n\n"
            "## Requirements\n\nVerify: Read the ready reference.\n\n"
            "## References\n\n- fixture/one.md\n- fixture/two.md\n\n"
            "## Notes\n\n- First note.\n- Second note.\n\n\n## Paths\n"
        )
        opened = (await ledger.read_board("orchestrator"))[1]
        assert len(opened) == 1
        assert opened[0].kind == "idle_worker"
        clock.advance(5)
        assert await runtime.tick_if_due()
        assert (await ledger.read_board("orchestrator"))[1] == opened
        await ledger.execute(
            "orchestrator",
            "lane_start",
            "start",
            {"lane": "lane", "worker": "fixture", "branch": "demo", "work_items": [mirror[0].ref]},
        )
        assert not (await ledger.read_board("orchestrator"))[1]
        with pytest.raises(LedgerError, match="already assigned"):
            await ledger.execute(
                "orchestrator",
                "lane_start",
                "duplicate",
                {
                    "lane": "duplicate",
                    "worker": "other",
                    "branch": "duplicate",
                    "work_items": [mirror[0].ref],
                },
            )
        assert {file: file.read_bytes() for file in tree.rglob("*") if file.is_file()} == original
    finally:
        await ledger.stop()
        await store.close()


async def test_slow_tick_skips_elapsed_intervals_and_removed_conditions_close(
    tmp_path: Path,
) -> None:
    store = SqliteStore("demo", tmp_path)
    await store.migrate()
    clock = FixedClock()
    workers = frozenset({"fixture"})
    catalog = build_catalog(workers)
    ledger = Ledger(store, catalog, clock, watcher_roles=("clerk",))
    ledger.start()

    class SlowSource:
        async def ready_items(self) -> tuple[WorkItem, ...]:
            clock.advance(8)
            return (WorkItem("1.1", "Ready"),)

        async def body(self, ref: str) -> str | None:
            return None

    try:
        runtime = ProjectRuntime(
            ledger,
            clock,
            tick_seconds=5,
            action_attempts=3,
            workers=workers,
            work_items=SlowSource(),
        )
        clock.advance(5)
        assert await runtime.tick_if_due()
        assert not await runtime.tick_if_due()
        assert {item.kind for item in (await ledger.read_board("orchestrator"))[1]} == {
            "idle_worker",
            "watcher_not_running",
        }
        await ledger.stop()
        ledger = Ledger(store, build_catalog(), clock)
        ledger.start()
        runtime = ProjectRuntime(ledger, clock, tick_seconds=5, action_attempts=3)
        clock.advance(5)
        assert await runtime.tick_if_due()
        assert not (await ledger.read_board("orchestrator"))[1]
        assert not await ledger.ready_work_items()
    finally:
        await ledger.stop()
        await store.close()


async def test_reader_timeout_cleans_up_after_large_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    installation = tmp_path / "trusted"
    script = installation / "_bmad" / "method" / "scripts" / "tickets.py"
    script.parent.mkdir(parents=True)
    script.write_text(
        "import time\nprint(chr(120) * 2_000_000, flush=True)\ntime.sleep(60)\n", encoding="utf-8"
    )
    tree = tmp_path / "mirror"
    tree.mkdir()
    monkeypatch.setattr(work_items_bmad, "_READER_TIMEOUT", 0.5)
    async with asyncio.timeout(3):
        with pytest.raises(TicketTreeError, match="timed out"):
            await BmadTicketTree(tree, installation).ready_items()
