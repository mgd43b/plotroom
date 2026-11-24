import asyncio
import sqlite3
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from threading import Event

import httpx
import pytest

from plotroom.adapters.store_sqlite import SqliteStore
from plotroom.app.config import ConfigError
from plotroom.app.serve import Daemon
from test_daemon import bearer, make_config, run_daemon, stop


async def test_backup_uses_project_scope_and_prunes_only_its_scheduled_copies(
    tmp_path: Path,
) -> None:
    root = tmp_path / "backups"
    root.mkdir()
    config = replace(make_config(tmp_path, "alpha", "beta"), backup_dir=root, backup_retain=2)
    daemon = Daemon(config)
    task, port = await run_daemon(daemon)
    try:
        async with httpx.AsyncClient(trust_env=False) as http:
            url = f"http://127.0.0.1:{port}/backup"
            assert (await http.post(url)).status_code == 401
            worker = bearer(config, "alpha", "worker:fixture")
            assert (
                await http.post(url, headers={"Authorization": f"Bearer {worker}"})
            ).status_code == 403
            token = bearer(config, "alpha")
            headers = {"Authorization": f"Bearer {token}"}
            assert (await http.post(url + "?project=beta", headers=headers)).status_code == 403
            names: list[str] = []
            for index in range(3):
                await daemon.ledgers["alpha"].execute(
                    "orchestrator",
                    "decide",
                    f"backup-{index}",
                    {
                        "who": "Fixture",
                        "exact_words": f"event {index}",
                        "governs": [],
                        "settles": [],
                        "where_ref": {"kind": "chat", "ref": "backup"},
                    },
                )
                response = await http.post(url, headers=headers, json={"project": "beta"})
                assert response.status_code == 201
                assert response.json()["project"] == "alpha"
                names.append(response.json()["file"])
                if index == 0:
                    (root / "alpha" / "unrelated.bak").write_text("fixture")
        assert not (root / "beta").exists()
        assert sorted(item.name for item in (root / "alpha").iterdir()) == sorted(
            [*names[1:], "unrelated.bak"]
        )
        for count, name in enumerate(names[1:], start=2):
            with closing(sqlite3.connect(root / "alpha" / name)) as db:
                assert db.execute("PRAGMA integrity_check").fetchone() == ("ok",)
                assert db.execute("SELECT count(*) FROM events").fetchone() == (count,)
    finally:
        await stop(daemon, task)


async def test_server_rejects_a_missing_backup_mount(tmp_path: Path) -> None:
    config = replace(make_config(tmp_path), backup_dir=tmp_path / "unmounted")
    with pytest.raises(ConfigError, match="existing writable directory"):
        await Daemon(config).run()
    assert not config.data_dir.exists()


async def test_store_close_waits_for_its_backup_worker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    entered, release = Event(), Event()

    def copy(_path: Path, _root: Path, _project: str, _retain: int) -> str:
        entered.set()
        assert release.wait(timeout=10)
        return "fixture.bak"

    monkeypatch.setattr("plotroom.adapters.store_sqlite.store.scheduled_backup", copy)
    store = SqliteStore("test", tmp_path)
    backup = asyncio.create_task(store.backup(tmp_path, 1))
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        closing_store = asyncio.create_task(store.close())
        await asyncio.sleep(0)
        assert not closing_store.done()
    finally:
        release.set()
    assert await backup == "fixture.bak"
    await closing_store
