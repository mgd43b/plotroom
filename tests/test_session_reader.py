import json
from pathlib import Path

import pytest

from fakes import FixedClock
from plotroom.adapters.claude_desktop import ClaudeDesktopReader
from plotroom.adapters.store_sqlite import SqliteStore
from plotroom.app.config import load_config
from plotroom.app.session_intake import SessionIntake
from plotroom.core.catalog import build_catalog
from plotroom.core.intake import SESSION_MESSAGE, TURN_REPORT, record_claude
from plotroom.core.ledger import Ledger
from plotroom.ports.session_reader import SessionEvent

SESSION = "synthetic-session"
REPORT = "Final assistant text"


def session_files(directory: Path, *, status: str = "idle") -> ClaudeDesktopReader:
    home = directory / "claude"
    desktop = directory / "desktop"
    registry = home / "sessions" / "103.json"
    state = desktop / "claude-code-sessions" / "account" / "org" / "local_fixture.json"
    transcript = home / "projects" / "encoded-project" / f"{SESSION}.jsonl"
    for path in (registry, state, transcript):
        path.parent.mkdir(parents=True, exist_ok=True)
    registry.write_text(
        json.dumps(
            {
                "sessionId": SESSION,
                "status": status,
                "statusUpdatedAt": 8,
                "messagingSocketPath": "/unused-fixture",
            }
        )
    )
    state.write_text(
        json.dumps(
            {
                "cliSessionId": SESSION,
                "completedTurns": 99,
                "lastTurnReport": "abridged",
            }
        )
    )
    (registry.parent / "103.key").write_bytes(b"\xff")
    rows = (
        {
            "type": "user",
            "turnPosition": {"turnIndex": 7},
            "uuid": "peer-record",
            "origin": {"kind": "peer", "body": "peer message"},
        },
        {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "tool_use", "name": "wrap_up", "input": {"recap": "different recap"}},
                ]
            },
        },
        {
            "type": "assistant",
            "message": {
                "stop_reason": "end_turn",
                "content": [
                    {"type": "text", "text": REPORT},
                ],
            },
        },
    )
    transcript.write_text("".join(json.dumps(row) + "\n" for row in rows) + '{"partial":')
    return ClaudeDesktopReader(home, desktop)


@pytest.mark.parametrize("reader_first", [True, False])
@pytest.mark.parametrize("index", [None, 7])
async def test_reader_and_ingest_observe_one_turn(
    tmp_path: Path, reader_first: bool, index: int | None
) -> None:
    reader = session_files(tmp_path)
    store = SqliteStore("demo", tmp_path)
    await store.migrate()
    ledger = Ledger(store, build_catalog(frozenset({"fixture"})), FixedClock())
    ledger.start()
    try:
        await ledger.execute(
            "orchestrator",
            "lane_start",
            "start",
            {"lane": "active", "worker": "fixture", "branch": "demo", "work_items": []},
        )
        intake = SessionIntake(ledger, reader, {SESSION: "fixture"}, 1)
        if reader_first:
            await intake.poll()
        appended = await ledger.run(
            "worker:fixture",
            lambda uow: record_claude(
                uow, SessionEvent(TURN_REPORT, SESSION, REPORT, turn_index=index)
            ),
        )
        assert appended.created is not reader_first
        await intake.poll()
        # A new reader process must also defer to the durable aliases.
        await SessionIntake(ledger, reader, {SESSION: "fixture"}, 1).poll()
        events = [event for event in await ledger.events_after(0) if event.source == "claude"]
        assert sorted(event.kind for event in events) == [SESSION_MESSAGE, TURN_REPORT]
        assert all(event.lane_id == "active" for event in events)
        assert next(event.body for event in events if event.kind == TURN_REPORT) == REPORT
        assert (
            next(event.body for event in events if event.kind == SESSION_MESSAGE) == "peer message"
        )
    finally:
        await ledger.stop()
        await store.close()


async def test_missing_metadata_and_open_turns_are_safe(tmp_path: Path) -> None:
    reader = session_files(tmp_path, status="busy")
    assert await reader.read("unbound") == ()
    assert await reader.read("../synthetic-session") == ()
    assert [event.kind for event in await reader.read(SESSION)] == [SESSION_MESSAGE]
    session_files(tmp_path)
    assert [event.kind for event in await reader.read(SESSION)] == [SESSION_MESSAGE, TURN_REPORT]


def test_desktop_config_uses_explicit_roots_and_session_binding(tmp_path: Path) -> None:
    config = tmp_path / "config.toml"
    config.write_text(
        '[projects.demo.session_reader]\nclaude_home = "claude"\napp_support = "desktop"\n'
        '[projects.demo.workers.fixture]\nclaude_session = "synthetic-session"\n'
    )
    settings = load_config(config, {}).session_readers["demo"]
    assert settings.claude_home == tmp_path / "claude"
    assert settings.app_support == tmp_path / "desktop"
    assert settings.sessions == {SESSION: "fixture"}
