"""The search epic's acceptance scenarios through authenticated loopback MCP."""

import asyncio
from dataclasses import replace
from pathlib import Path
from typing import Any

from mcp.client.client import Client
from mcp_types import TextContent

from conftest import authenticated_client, tool_json
from fakes import FixedClock
from plotroom.adapters.store_sqlite import SqliteStore, head_revision
from plotroom.adapters.store_sqlite.migrate import alembic_config, upgrade_to_head
from plotroom.app.config import EmbedderConfig
from plotroom.app.serve import Daemon
from plotroom.core.catalog import build_catalog
from plotroom.core.ledger import Ledger
from plotroom.core.snapshot import estimate_tokens
from plotroom.ports.search import DECISION_KIND, MESSAGE_KIND, BackfillSource, EmbeddingModel
from test_daemon import bearer, make_config, run_daemon, stop
from test_migrations import _migrations_at  # pyright: ignore[reportPrivateUsage]
from test_search import message
from test_search_embeddings import MODEL, Endpoint, await_cursor
from test_search_embeddings import endpoint as endpoint

SEARCH_BUDGET = 256


async def search(client: Client, text: str, **arguments: Any) -> dict[str, Any]:
    result = await client.call_tool("search", {"text": text, **arguments})
    content = result.content[0]
    assert isinstance(content, TextContent)
    assert estimate_tokens(content.text) <= SEARCH_BUDGET
    payload = tool_json(result)
    assert payload["ok"], payload
    data: dict[str, Any] = payload["data"]
    return data


async def test_full_text_excerpts_are_project_scoped_and_budgeted(tmp_path: Path) -> None:
    config = replace(make_config(tmp_path, "demo", "other"), search_budget=SEARCH_BUDGET)
    demo_token, other_token = bearer(config), bearer(config, "other")
    daemon = Daemon(config)
    task, port = await run_daemon(daemon)
    url = f"http://127.0.0.1:{port}/mcp"
    try:
        demo, other = daemon.ledgers["demo"], daemon.ledgers["other"]
        first = (await demo.append("system", message("shared", "telescope on the roof"))).event
        foreign = (await other.append("system", message("shared", "telescope by the gate"))).event
        long_body = "budgetword " + "界" * 260
        budget_ids = {
            (await demo.append("system", message(f"budget-{index}", long_body))).event.id
            for index in range(4)
        }
        await other.append("system", message("budget", "budgetword from the other project"))

        async with authenticated_client(url, demo_token) as client:
            assert await search(client, "telescope") == {
                "hits": [{"id": first.id, "kind": MESSAGE_KIND, "excerpt": first.body}],
                "omitted": 0,
            }
            result = await search(client, "budgetword", limit=20)
            assert 0 < len(result["hits"]) < len(budget_ids)
            assert result["omitted"] == len(budget_ids) - len(result["hits"])
            for hit in result["hits"]:
                assert set(hit) == {"id", "kind", "excerpt"}
                assert hit["id"] in budget_ids
                assert hit["kind"] == MESSAGE_KIND
                assert "budgetword" in hit["excerpt"]
                assert hit["excerpt"] in long_body

        async with authenticated_client(url, other_token) as client:
            assert await search(client, "telescope") == {
                "hits": [{"id": foreign.id, "kind": MESSAGE_KIND, "excerpt": foreign.body}],
                "omitted": 0,
            }
    finally:
        await stop(daemon, task)


async def decide(client: Client, request_id: str, exact_words: str) -> str:
    result = await client.call_tool(
        "decide",
        {
            "request_id": request_id,
            "who": "fixture-human",
            "exact_words": exact_words,
            "where_ref": {"kind": "chat", "ref": request_id},
            "governs": [],
            "settles": [],
        },
    )
    payload = tool_json(result)
    assert payload["ok"], payload
    decision_id: str = payload["data"]["decision_id"]
    return decision_id


async def semantic_query(
    client: Client, endpoint: Endpoint, model: EmbeddingModel, text: str, vector: list[float]
) -> dict[str, Any]:
    async with asyncio.timeout(10), asyncio.TaskGroup() as tasks:
        pending = tasks.create_task(search(client, text, kinds=[DECISION_KIND], limit=1))
        (await endpoint.next_request(model, text)).vectors(vector)
    return pending.result()


async def await_backfill(ledger: Ledger, store: SqliteStore, source: BackfillSource) -> None:
    async with asyncio.timeout(5):
        while True:
            committed = ledger.commit_signal
            if await store.backfill_position(source) is None:
                return
            await committed.wait()


async def test_paraphrased_decision_is_found_again_after_model_reindex(
    tmp_path: Path, endpoint: Endpoint
) -> None:
    config = replace(make_config(tmp_path), search_budget=SEARCH_BUDGET)
    token = bearer(config)
    exact_words = "seal the hatch"
    other_words = "prepare supper"
    paraphrase = "secure doorway"
    destination = EmbeddingModel("replacement-fixture-model", MODEL.dimension)
    daemon = Daemon(replace(config, embedders={"demo": EmbedderConfig(endpoint.url, MODEL)}))
    task, port = await run_daemon(daemon)
    reader = SqliteStore("demo", config.data_dir)
    try:
        async with authenticated_client(f"http://127.0.0.1:{port}/mcp", token) as client:
            decision = await decide(client, "fixture-hatch", exact_words)
            (await endpoint.next_request(MODEL, exact_words)).vectors([1, 0])
            await decide(client, "fixture-supper", other_words)
            (await endpoint.next_request(MODEL, other_words)).vectors([0, 1])
            ledger = daemon.ledgers["demo"]
            await await_cursor(ledger, reader.embedding_index(MODEL), 2)
            await await_backfill(ledger, reader, BackfillSource.DECISIONS)
            expected = {
                "hits": [{"id": decision, "kind": DECISION_KIND, "excerpt": exact_words}],
                "omitted": 1,
                "mode": "semantic",
            }
            assert await semantic_query(client, endpoint, MODEL, paraphrase, [1, 0]) == expected
    finally:
        await reader.close()
        await stop(daemon, task)

    daemon = Daemon(replace(config, embedders={"demo": EmbedderConfig(endpoint.url, destination)}))
    task, port = await run_daemon(daemon)
    reader = SqliteStore("demo", config.data_dir)
    try:
        async with authenticated_client(f"http://127.0.0.1:{port}/mcp", token) as client:
            request = await endpoint.next_request(destination, exact_words, other_words)
            assert await search(client, paraphrase, kinds=[DECISION_KIND]) == {
                "hits": [],
                "omitted": 0,
                "mode": "lexical",
                "fallback_reason": "index_pending",
            }
            # Change the coordinate space. A query against the old vectors would rank supper first.
            request.vectors([0, 1], [1, 0])
            await await_cursor(daemon.ledgers["demo"], reader.embedding_index(destination), 2)
            assert (
                await semantic_query(client, endpoint, destination, paraphrase, [0, 1]) == expected
            )
    finally:
        await reader.close()
        await stop(daemon, task)


async def test_daemon_backfills_the_oldest_record_during_historical_upgrade(tmp_path: Path) -> None:
    config = replace(make_config(tmp_path), search_budget=SEARCH_BUDGET)
    config.data_dir.mkdir()
    historical = alembic_config(_migrations_at(tmp_path / "before-search", "0017"))
    store = SqliteStore("demo", config.data_dir)
    old = Ledger(store, build_catalog(), FixedClock())
    try:
        await upgrade_to_head(store._engine, historical)  # pyright: ignore[reportPrivateUsage]
        assert await store.revision() == "0017"
        old.start()
        oldest = (await old.append("system", message("oldest", "the original observatory"))).event
        newer = (await old.append("system", message("newer", "the replacement observatory"))).event
        assert oldest.seq < newer.seq
    finally:
        await old.stop()
        await store.close()

    daemon = Daemon(config)
    token = bearer(config)
    task, port = await run_daemon(daemon)
    reader = SqliteStore("demo", config.data_dir)
    try:
        assert await reader.revision() == head_revision()
        await await_backfill(daemon.ledgers["demo"], reader, BackfillSource.EVENTS)
        async with authenticated_client(f"http://127.0.0.1:{port}/mcp", token) as client:
            assert await search(client, "original observatory") == {
                "hits": [{"id": oldest.id, "kind": MESSAGE_KIND, "excerpt": oldest.body}],
                "omitted": 0,
            }
    finally:
        await reader.close()
        await stop(daemon, task)
