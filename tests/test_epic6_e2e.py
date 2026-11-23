"""Cover Epic 6's two Done-when lines over local HTTP and stdio."""

import asyncio
from pathlib import Path
from typing import Any

import pytest

from conftest import BRIEF_TICKET_BODY, BRIEF_TICKET_REF, make_brief_ticket, write_config
from plotroom.adapters.github import GitHubPoller
from plotroom.adapters.work_items_bmad import BmadTicketTree
from plotroom.app.config import load_config
from plotroom.app.tokens import TokenRegistry
from test_epic3_e2e import REPO, LocalPlotroom, observe_pull, tools

pytestmark = pytest.mark.parametrize("transport", ["http", "stdio"])


@pytest.fixture
def epic6(tmp_path: Path, token_home: Path, monkeypatch: pytest.MonkeyPatch) -> LocalPlotroom:
    async def pause_github(_self: GitHubPoller, _details: Any = None) -> None:
        await asyncio.Future[None]()

    monkeypatch.setattr(GitHubPoller, "run", pause_github)
    tree, installation = make_brief_ticket(tmp_path)
    config = write_config(tmp_path)
    with config.open("a", encoding="utf-8") as output:
        output.write(
            f'ticket_tree = "{tree}"\nbmad_installation = "{installation}"\n'
            "[projects.demo.workers.alpha]\n"
            "[projects.demo.workers.beta]\n"
            "[projects.demo.workers.gamma]\n"
            "[projects.demo.brief]\n"
            'standing_rules = ["Use local fixtures.", "Record verification evidence."]\n'
            'plan = "Name the tasks."\nresult = "Report the checks."\n'
            'template = "Ticket {work_item} for {worker}\\n{source_text}\\nPaths\\n{paths}'
            "\\nDon't touch\\n{dont_touch}\\nPlan\\n{plan}\\nRESULT\\n{result}"
            '\\nRules\\n{standing_rules}\\n"\n'
            f'[projects.demo.github]\nrepository = "{REPO}"\n'
        )
    registry = TokenRegistry(load_config(config, {}).data_dir)
    return LocalPlotroom(
        config,
        registry.issue("demo", "orchestrator", local=True),
        registry.issue("demo", "drain", local=True),
        registry.issue("demo", "worker:alpha", local=True),
        (),
    )


async def test_brief_uses_mirrored_ticket_live_claims_and_rules(
    epic6: LocalPlotroom, transport: str
) -> None:
    async with (
        asyncio.timeout(15),
        epic6.serve() as (endpoint, ledger),
        tools(endpoint, transport) as call,
        tools(endpoint, transport, token=endpoint.worker) as worker,
    ):
        source = load_config(epic6.config, {}).ticket_trees["demo"]
        items = await BmadTicketTree(*source).ready_items()
        assert [item.ref for item in items] == [BRIEF_TICKET_REF]
        await ledger.run("system", lambda uow: uow.replace_work_items(items))
        assert await ledger.ready_work_items() == items
        for name in ("beta", "gamma"):
            await call(
                "lane_start",
                request_id=f"start-{name}",
                lane=f"{name}-lane",
                worker=name,
                branch=name,
                work_items=[],
            )
            await call(
                "claim",
                request_id=f"claim-{name}",
                lane=f"{name}-lane",
                globs=[f"{name}/**"],
                reason=f"{name} scope",
            )
        expected = (
            f"Ticket {BRIEF_TICKET_REF} for alpha\n{BRIEF_TICKET_BODY}"
            "\nPaths\n\nDon't touch\n- beta/** (beta-lane: beta scope)\n"
            "- gamma/** (gamma-lane: gamma scope)\nPlan\nName the tasks.\n"
            "RESULT\nReport the checks.\nRules\n"
            "- Use local fixtures.\n- Record verification evidence.\n"
        )
        args = {"work_item": BRIEF_TICKET_REF, "worker": "alpha"}
        assert await call("brief", **args) == {"brief": expected}
        await worker("brief", **args, error="forbidden")
        await call("release", request_id="release-beta", lane="beta-lane", globs=["beta/**"])
        assert await call("brief", **args) == {
            "brief": expected.replace("- beta/** (beta-lane: beta scope)\n", "")
        }


async def test_merged_ready_checklist_guards_archive_and_proposes_it_when_empty(
    epic6: LocalPlotroom, transport: str
) -> None:
    lane = "alpha-lane"
    steps = [
        {"step": "Check smoke", "owner": "alpha", "ref": "smoke"},
        {"step": "Verify main", "owner": "alpha", "ref": "main"},
        {"step": "Record evidence", "owner": "orchestrator", "ref": "ticket"},
    ]
    async with (
        asyncio.timeout(15),
        epic6.serve() as (endpoint, ledger),
        tools(endpoint, transport) as call,
        tools(endpoint, transport, token=endpoint.worker) as worker,
    ):
        await call(
            "lane_start",
            request_id="start",
            lane=lane,
            worker="alpha",
            branch="branch-1",
            work_items=[BRIEF_TICKET_REF],
        )
        await observe_pull(ledger, 1, "open")
        await worker(
            "lane_ready",
            request_id="ready",
            lane=lane,
            pr=1,
            head_sha=f"{1:040x}",
            checklist=steps,
        )
        pending = (await call("snapshot", lane=lane))["checklist"]
        assert pending == {
            "version": 1,
            "pr": 1,
            "head_sha": f"{1:040x}",
            "state": "pending",
            "steps": [{**step, "completion_ref": None} for step in steps],
        }
        await observe_pull(ledger, 1, "merged")
        active = await call("snapshot", lane=lane)
        assert active["checklist"] == {**pending, "state": "active"}
        assert (active["pr"], active["head_sha"]) == (1, f"{1:040x}")
        refusal = await call("lane_archive", request_id="blocked", lane=lane, error="conflict")
        assert refusal["message"] == (
            "the lane has open checklist steps: 'Check smoke', 'Verify main', 'Record evidence'"
        )
        for index, row in enumerate(steps[:-1]):
            await worker("checklist_done", request_id=f"step-{index}", lane=lane, step=row["step"])
        assert not any(item["kind"] == "archive_lane" for item in (await call("next"))["items"])
        await call("checklist_done", request_id="last", lane=lane, step=steps[-1]["step"])
        [proposal] = [
            item for item in (await call("next"))["items"] if item["kind"] == "archive_lane"
        ]
        assert proposal["subject_ref"] == lane
        await observe_pull(ledger, 1, "merged")
        assert proposal == await call("next", item=proposal["id"])
        assert (await call("lane_archive", request_id="archive", lane=lane))["state"] == "archived"
        await call("next", item=proposal["id"], error="not_found")
