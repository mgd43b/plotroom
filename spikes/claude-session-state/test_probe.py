"""Tests for probe.py on a hand-written synthetic fixture (no real session data)."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from unittest import mock

import probe

CLI_ID = "cli-session-0001"


def user(turn: int, origin: dict | None, turn_origin: str) -> dict:
    row = {
        "type": "user",
        "turnPosition": {"promptIndex": 0, "turnIndex": turn},
        "turnOrigin": turn_origin,
    }
    if origin is not None:
        row["origin"] = origin
    return row


def assistant(text: str | None, stop: str, tool: tuple[str, dict] | None = None) -> dict:
    blocks: list[dict] = []
    if text is not None:
        blocks.append({"type": "text", "text": text})
    if tool:
        blocks.append({"type": "tool_use", "name": tool[0], "input": tool[1]})
    return {"type": "assistant", "message": {"stop_reason": stop, "content": blocks}}


ROWS = [
    user(1, {"kind": "human"}, "human"),
    assistant(None, "tool_use", ("Bash", {"command": "true"})),
    assistant("first report", "end_turn"),
    {"type": "system", "subtype": "stop_hook_summary"},
    user(2, {"kind": "peer", "from": "sender-x", "name": "n", "body": "hello there"}, "peer"),
    assistant("done", "end_turn", ("mcp__x__wrap_up", {"recap": "the recap"})),
    {"type": "system", "subtype": "stop_hook_summary"},
    {"type": "system", "subtype": "stop_hook_summary"},  # a Stop hook that continued the turn
    user(3, {"kind": "human"}, "human"),
    assistant("working", "tool_use"),
]


class ProbeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        home = os.path.join(self.tmp.name, "claude")
        app = os.path.join(self.tmp.name, "app")
        os.makedirs(os.path.join(home, "sessions"))
        os.makedirs(os.path.join(home, "projects", "some-project"))
        os.makedirs(os.path.join(app, "claude-code-sessions", "acct", "org"))
        self.home, self.app = home, app
        self.write(
            os.path.join(home, "sessions", "1.json"), {"sessionId": CLI_ID, "status": "busy"}
        )
        # A key file beside the registry must never be opened or parsed.
        self.write_raw(os.path.join(home, "sessions", "1.abc.key"), "not json, not read")
        self.write(
            os.path.join(app, "claude-code-sessions", "acct", "org", "local_1.json"),
            {
                "sessionId": "local_1",
                "cliSessionId": CLI_ID,
                "completedTurns": 2,
                "lastTurnReport": "r" * 40,
                "lastTurnEdgeAt": 1700000000000,
            },
        )
        self.write_raw(
            os.path.join(home, "projects", "some-project", f"{CLI_ID}.jsonl"),
            "\n".join(json.dumps(r) for r in ROWS)
            + '\n{"type": "assistant", "mess',  # cut-off last row
        )

    def write(self, path: str, data: dict) -> None:
        self.write_raw(path, json.dumps(data))

    def write_raw(self, path: str, text: str) -> None:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)

    def test_turns_group_by_turn_index_and_survive_a_cut_off_row(self) -> None:
        grouped = probe.turns(probe.read_rows(probe.transcript_path(self.home, CLI_ID) or ""))
        self.assertEqual([t["turn"] for t in grouped], [1, 2, 3])
        self.assertEqual(grouped[0]["report"], "first report")
        self.assertEqual(grouped[1]["recap"], "the recap")
        self.assertEqual(grouped[1]["stop_hook_rows"], 2)
        self.assertEqual(grouped[1]["inbound"]["from"], "sender-x")
        self.assertIsNone(grouped[0]["inbound"])

    def test_open_turn_is_not_reported_until_the_process_is_idle(self) -> None:
        rows = probe.read_rows(probe.transcript_path(self.home, CLI_ID) or "")
        busy = list(probe.events("s", rows, "busy", include_text=False))
        self.assertEqual([e["turn"] for e in busy if e["kind"] == "claude.turn_report"], [1, 2])
        idle = list(probe.events("s", rows, "idle", include_text=False))
        self.assertEqual([e["turn"] for e in idle if e["kind"] == "claude.turn_report"], [1, 2, 3])

    def test_session_message_event_and_recap_preferred_over_final_text(self) -> None:
        rows = probe.read_rows(probe.transcript_path(self.home, CLI_ID) or "")
        out = list(probe.events("s", rows, "busy", include_text=True))
        message = [e for e in out if e["kind"] == "claude.session_message"]
        self.assertEqual(len(message), 1)
        self.assertEqual(message[0]["body"], "hello there")
        report = next(e for e in out if e["kind"] == "claude.turn_report" and e["turn"] == 2)
        self.assertEqual(report["report"], "the recap")
        self.assertTrue(report["has_recap"])

    def test_text_is_left_out_by_default(self) -> None:
        rows = probe.read_rows(probe.transcript_path(self.home, CLI_ID) or "")
        for event in probe.events("s", rows, "idle", include_text=False):
            self.assertNotIn("report", event)
            self.assertNotIn("body", event)

    def test_survey_reports_counts_and_no_identifiers(self) -> None:
        result = probe.survey(self.home, self.app)
        self.assertEqual(result["registry_files"], 1)
        (only,) = result["sessions"]
        self.assertEqual(only["turn_groups"], 3)
        self.assertEqual(only["completed_turns_field"], 2)
        self.assertEqual(only["app_state_report_len"], 40)
        self.assertTrue(only["has_transcript"])
        text = json.dumps(result)
        self.assertNotIn(CLI_ID, text)
        self.assertNotIn("sender-x", text)

    def test_app_state_event(self) -> None:
        state = probe.app_state(self.app)[CLI_ID]
        event = probe.app_state_event("s", state, include_text=False)
        assert event is not None
        self.assertEqual(event["turn_edge_at_ms"], 1700000000000)
        self.assertEqual(event["report_len"], 40)
        self.assertIsNone(probe.app_state_event("s", {"lastTurnReport": "x"}, include_text=False))

    def test_interrupted_closed_turn_says_it_has_no_end_turn(self) -> None:
        rows = probe.read_rows(probe.transcript_path(self.home, CLI_ID) or "")
        reports = [
            e for e in probe.events("s", rows, "idle", False) if e["kind"] == "claude.turn_report"
        ]
        self.assertEqual([e["has_end_turn"] for e in reports], [True, True, False])

    def test_unknown_values_never_reach_default_output(self) -> None:
        self.write(
            os.path.join(self.home, "sessions", "2.json"),
            {"sessionId": "other-cli-id", "status": "SECRET-VALUE"},
        )
        self.write(os.path.join(self.home, "sessions", "3.json"), {"sessionId": "no-status-cli-id"})
        text = json.dumps(probe.survey(self.home, self.app), sort_keys=True)
        self.assertNotIn("SECRET-VALUE", text)
        self.assertEqual(probe.survey(self.home, self.app)["registry_statuses"]["other"], 2)

    def test_key_files_are_never_opened(self) -> None:
        real_open = open
        opened: list[str] = []

        def spy(path, *args, **kwargs):  # type: ignore[no-untyped-def]
            opened.append(str(path))
            return real_open(path, *args, **kwargs)

        with mock.patch("builtins.open", spy):
            probe.registry(self.home)
            probe.survey(self.home, self.app)
        self.assertTrue(opened)
        self.assertFalse([p for p in opened if p.endswith(".key")])

    def test_missing_files_are_not_errors(self) -> None:
        empty = os.path.join(self.tmp.name, "nothing")
        self.assertEqual(probe.survey(empty, empty)["registry_files"], 0)
        self.assertIsNone(probe.transcript_path(empty, CLI_ID))


if __name__ == "__main__":
    unittest.main()
