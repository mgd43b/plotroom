import shutil
from pathlib import Path

import pytest

from fakes import FixedClock
from plotroom.adapters.store_sqlite import SqliteStore
from plotroom.adapters.work_items_bmad import BmadTicketTree, TicketTreeError
from plotroom.app.runtime import ProjectRuntime
from plotroom.core.catalog import build_catalog
from plotroom.core.ledger import Ledger
from plotroom.ports.work_items import WorkItem


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
    ledger = Ledger(store, build_catalog(workers), clock)
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
        assert {file: file.read_bytes() for file in tree.rglob("*") if file.is_file()} == original
    finally:
        await ledger.stop()
        await store.close()
