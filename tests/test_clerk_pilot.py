"""Rehearse the clerk's local workflow with a read-only mock provider and in-memory merges."""

import asyncio
import json
import sys
from collections.abc import Generator
from contextlib import contextmanager, suppress
from dataclasses import replace
from datetime import datetime
from pathlib import Path

import pytest

from conftest import authenticated_client, child_env, write_config
from fakes import FixedClock
from plotroom.app.config import GitHubConfig, load_config
from plotroom.app.serve import Daemon
from plotroom.app.tokens import TokenRegistry
from plotroom.core.merge_controller import MERGE_OBSERVED, MERGE_READY, OUTSIDE_GATE_MERGE
from plotroom.core.merge_gate import GatePolicy
from plotroom.core.triggers import TRIGGER_FIRED
from plotroom.core.watchers import Watchers
from test_merge_controller_e2e import (
    API,
    LATER,
    MAIN,
    REPO,
    WORKFLOW,
    Pull,
    Repository,
    Row,
    call,
    events,
    next_items,
)
from test_merge_controller_e2e import repository as repository

FIXTURES = Path(__file__).parent / "fixtures" / "clerk_import"


async def cli(config: Path, token_id: str, command: str, *arguments: str) -> Row:
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "plotroom",
        command,
        "--config",
        str(config),
        "--token-id",
        token_id,
        *arguments,
        env=child_env(),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), 10)
        assert process.returncode == 0, stderr.decode()
        result: Row = json.loads(stdout)
        return result
    finally:
        if process.returncode is None:
            with suppress(ProcessLookupError):
                process.kill()
            await process.wait()


@pytest.mark.usefixtures("token_home")
async def test_local_import_watch_and_pinned_fixture_merge(
    tmp_path: Path, repository: Repository, monkeypatch: pytest.MonkeyPatch
) -> None:
    pending = Pull(401, checks=("success", None), lane="fixture/projected-head")
    held = Pull(402, threads=1)
    repository.add(pending, held)
    watching = asyncio.Event()
    waiting = Watchers.waiting

    @contextmanager
    def observed_wait(watchers: Watchers, role: str) -> Generator[None]:
        with waiting(watchers, role):
            if role == "clerk":
                watching.set()
            yield

    monkeypatch.setattr(Watchers, "waiting", observed_wait)

    config_path = write_config(tmp_path, projects=("epic4",))
    secret = tmp_path / "fixture-provider-token"
    secret.write_text("fixture", encoding="utf-8")
    config = replace(
        load_config(config_path, {}),
        workers={"epic4": frozenset({"worker"})},
        watchers={"epic4": ("clerk",)},
        github={
            "epic4": GitHubConfig(
                REPO,
                secret,
                API,
                60,
                merge_gate_policy=GatePolicy(2, ci_workflow=WORKFLOW),
            )
        },
    )
    tokens = TokenRegistry(config.data_dir)
    orchestrator = tokens.issue("epic4", "orchestrator")
    clerk = tokens.issue("epic4", "clerk")
    daemon = Daemon(config, clock=FixedClock(datetime.fromisoformat(LATER)))
    serving = asyncio.create_task(daemon.run())
    try:
        port = await asyncio.wait_for(daemon.wait_started(), 10)
        url = f"http://127.0.0.1:{port}/mcp"
        async with (
            authenticated_client(url, orchestrator.bearer) as owner,
            authenticated_client(url, clerk.bearer) as reviewer,
        ):
            await repository.refresh()
            imported = await cli(
                config_path,
                orchestrator.token_id,
                "import",
                "--triggers",
                str(FIXTURES / "pilot-triggers.md"),
                "--ledger",
                str(FIXTURES / "pilot-ledger.md"),
            )
            trigger_id = imported["triggers"]["T1"]
            assert imported["lanes"]["401"]
            assert imported["items"]["skip:402"]
            assert not await events(daemon, TRIGGER_FIRED)
            assert not await next_items(reviewer, MERGE_READY)

            cursor = (await call(reviewer, "next"))["cursor"]
            async with asyncio.TaskGroup() as tasks:
                watcher = tasks.create_task(
                    cli(config_path, clerk.token_id, "watch", "--cursor", str(cursor))
                )
                await asyncio.wait_for(watching.wait(), 10)
                pending.checks = ("success", "success")
                await repository.refresh()
            wake = watcher.result()
            assert wake["cursor"] > cursor
            (offer,) = await next_items(reviewer, MERGE_READY)
            assert (wake["item"]["id"], wake["item"]["state"]) == (offer["id"], "open")
            pins = offer["context"]["merge_gate"]
            assert pins == {"pr": 401, "expected_head_sha": pending.head, "expected_main_sha": MAIN}
            verdict = await call(reviewer, "merge_gate", **pins)
            merged_sha = repository.execute_clerk_merge(offer["context"]["command"], pins, verdict)

            held.merge_commit = "f" * 40  # An outside merge on a fixture PR with no open offer.
            await repository.refresh()
            await repository.refresh()
            observed = await events(daemon, MERGE_OBSERVED)
            assert len(observed) == 2
            assert {
                event.fields["number"]: (event.fields["classification"], event.fields["merge_sha"])
                for event in observed
            } == {401: ("gated", merged_sha), 402: ("outside_gate", held.merge_commit)}
            (outside,) = await next_items(owner, OUTSIDE_GATE_MERGE)
            assert outside["context"]["number"] == 402
            assert not await next_items(reviewer, MERGE_READY)
            (fired,) = await events(daemon, TRIGGER_FIRED)
            assert fired.fields["trigger_id"] == trigger_id
    finally:
        daemon.stop()
        await asyncio.wait_for(serving, 10)
