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
