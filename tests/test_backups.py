import sqlite3
from contextlib import closing
from dataclasses import replace
from pathlib import Path

import httpx

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
