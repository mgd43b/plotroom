"""Tests for probe.py on a hand-written synthetic fixture (no real session data)."""

from __future__ import annotations

import io
import json
import os
import stat
import tempfile
import unittest
import weakref
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

import probe

CLI_ID = "cli-session-0001"


def user(turn: object, origin: dict | None, turn_origin: str) -> dict:
    row = {
        "type": "user",
        "turnPosition": {"promptIndex": 0, "turnIndex": turn},
        "turnOrigin": turn_origin,
    }
    if origin is not None:
        row["origin"] = origin
    return row


def assistant(text: object, stop: str, tool: tuple[str, object] | None = None) -> dict:
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
        path = probe.transcript_path(self.home, CLI_ID) or ""
        grouped = list(probe.transcript_items(probe.read_rows(path)))
        self.assertEqual([t["turn"] for t in grouped], [1, 2, 3])
        self.assertEqual(grouped[0]["report"], "first report")
        self.assertEqual(grouped[1]["recap"], "the recap")
        self.assertEqual(grouped[1]["stop_hook_rows"], 2)
        self.assertEqual(grouped[1]["body"], "hello there")
        self.assertIsNone(grouped[0]["body"])

    def test_open_turn_is_not_reported_until_the_process_is_idle(self) -> None:
        path = probe.transcript_path(self.home, CLI_ID) or ""
        busy = list(probe.events("s", probe.read_rows(path), lambda: False, include_text=False))
        self.assertEqual([e["turn"] for e in busy if e["kind"] == "claude.turn_report"], [1, 2])
        idle = list(probe.events("s", probe.read_rows(path), lambda: True, include_text=False))
        self.assertEqual([e["turn"] for e in idle if e["kind"] == "claude.turn_report"], [1, 2, 3])

    def test_session_message_event_and_recap_preferred_over_final_text(self) -> None:
        rows = probe.read_rows(probe.transcript_path(self.home, CLI_ID) or "")
        out = list(probe.events("s", rows, lambda: False, include_text=True))
        message = [e for e in out if e["kind"] == "claude.session_message"]
        self.assertEqual(len(message), 1)
        self.assertEqual(message[0]["body"], "hello there")
        report = next(e for e in out if e["kind"] == "claude.turn_report" and e["turn"] == 2)
        self.assertEqual(report["report"], "the recap")
        self.assertTrue(report["has_recap"])

    def test_text_is_left_out_by_default(self) -> None:
        rows = probe.read_rows(probe.transcript_path(self.home, CLI_ID) or "")
        for event in probe.events("s", rows, lambda: True, include_text=False):
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

    def test_boolean_app_state_edge_is_not_reported(self) -> None:
        path = os.path.join(self.app, "claude-code-sessions", "acct", "org", "local_1.json")
        state = probe.app_state(self.app)[CLI_ID]
        (session,) = probe.survey(self.home, self.app)["sessions"]
        code, events, _ = self.run_events(session["session_handle"])
        self.assertEqual(code, 0)
        self.assertTrue(any(event.get("source") == "app_state" for event in events))
        self.write(path, {**state, "lastTurnEdgeAt": True})
        (session,) = probe.survey(self.home, self.app)["sessions"]
        self.assertFalse(session["app_state_has_turn_edge"])
        code, events, _ = self.run_events(session["session_handle"])
        self.assertEqual(code, 0)
        self.assertTrue(all(event.get("source") != "app_state" for event in events))

    def test_interrupted_closed_turn_says_it_has_no_end_turn(self) -> None:
        rows = probe.read_rows(probe.transcript_path(self.home, CLI_ID) or "")
        reports = [
            e
            for e in probe.events("s", rows, lambda: True, False)
            if e["kind"] == "claude.turn_report"
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

    def run_events(self, handle: str, *flags: str) -> tuple[int, list[dict], str]:
        with redirect_stdout(io.StringIO()) as stdout, redirect_stderr(io.StringIO()) as stderr:
            code = probe.main(
                [
                    "events",
                    "--claude-home",
                    self.home,
                    "--app-support",
                    self.app,
                    "--session",
                    handle,
                    *flags,
                ]
            )
        return (
            code,
            [json.loads(line) for line in stdout.getvalue().splitlines()],
            stderr.getvalue(),
        )

    def test_mixed_rows_are_safe_and_unindexed_peers_are_reported(self) -> None:
        path = probe.transcript_path(self.home, CLI_ID)
        assert path is not None
        bad_indices = [
            user(index, {"kind": "peer", "body": "unindexed-private"}, "peer")
            for index in (True, 0, -1, "index-private", {"index-private": 1})
        ]
        rows = [
            {"type": "user", "origin": {"kind": "peer", "body": "before-private"}},
            ROWS[0],
            {"type": "assistant", "message": ["assistant-private"]},
            assistant("valid-private", "end_turn", ("mcp__x__wrap_up", ["bad-input"])),
            assistant({"bad-text": "assistant-private"}, "end_turn"),
            *bad_indices,
            assistant("unattributed-private", "end_turn"),
            ROWS[4],
            ROWS[5],
            ROWS[8],
            {"type": "user", "origin": {"kind": "peer", "body": "while-busy-private"}},
        ]
        with open(path, "wb") as fh:
            fh.write(b'{"type": "assistant", "message":"\xe2\x82\n')
            for row in rows:
                fh.write(json.dumps(row).encode("utf-8") + b"\n")
            fh.write(b'{"type": "assistant", "message":"\xe2\x82')

        (session,) = probe.survey(self.home, self.app)["sessions"]
        self.assertEqual(session["turn_groups"], 3)
        self.assertEqual(session["reports_found"], 2)
        self.assertEqual(session["inbound_peer_messages"], 8)
        self.assertNotIn("private", json.dumps(session))
        code, events, err = self.run_events(session["session_handle"])
        self.assertEqual((code, err), (0, ""))
        self.assertNotIn("private", json.dumps(events))
        self.assertNotIn(CLI_ID, json.dumps(events))
        messages = [event for event in events if event["kind"] == "claude.session_message"]
        self.assertEqual([event["turn"] for event in messages], [None] * 6 + [2, None])
        self.assertEqual(messages[0]["body_len"], len("before-private"))
        self.assertTrue(all("body" not in event for event in messages))
        self.assertTrue(all("report" not in event for event in events))
        _, with_text, _ = self.run_events(session["session_handle"], "--include-text")
        first_report = next(event for event in with_text if event.get("turn") == 1)
        self.assertEqual(first_report["report"], "valid-private")

    def test_survey_counts_stream_without_retaining_historical_turns(self) -> None:
        class Text(str):
            pass

        def rows():
            text = Text("historical peer body")
            ref = weakref.ref(text)
            yield user(1, {"kind": "peer", "body": text}, "peer")
            del text
            yield assistant("historical report", "end_turn")
            yield user(2, None, "human")
            yield user(3, None, "human")
            self.assertIsNone(ref())

        with mock.patch.object(probe, "read_rows", side_effect=lambda path: rows()):
            (session,) = probe.survey(self.home, self.app)["sessions"]
        self.assertEqual(session["turn_groups"], 3)
        self.assertEqual(session["reports_found"], 1)

    def test_reader_yields_before_consuming_another_row(self) -> None:
        def lines():
            yield b'{"index": 1}\n'
            self.fail("reader consumed a second line before yielding the first")

        with mock.patch("builtins.open") as stream:
            stream.return_value.__enter__.return_value = lines()
            rows = probe.read_rows("unused")
            try:
                self.assertEqual(next(rows), {"index": 1})
            finally:
                rows.close()

    def test_missing_peer_index_and_malformed_position_end_attribution(self) -> None:
        for boundary in (
            {"type": "user", "origin": {"kind": "peer", "body": "no index"}},
            {"type": "user", "turnPosition": ["malformed"]},
        ):
            with self.subTest(boundary=boundary):
                rows = [
                    user(1, None, "human"),
                    {"type": "user", "message": {"content": [{"type": "tool_result"}]}},
                    assistant("indexed report", "end_turn"),
                    boundary,
                    assistant("unattributed report", "end_turn"),
                    user(2, None, "human"),
                ]
                events = list(probe.events("s", rows, lambda: False, include_text=True))
                reports = [event for event in events if event["kind"] == "claude.turn_report"]
                self.assertEqual(
                    [(event["turn"], event["report"]) for event in reports],
                    [(1, "indexed report")],
                )

    def test_events_handle_survives_reordering_and_rejects_missing_or_duplicate(self) -> None:
        (session,) = probe.survey(self.home, self.app)["sessions"]
        handle = session["session_handle"]
        self.assertNotIn(CLI_ID, handle)
        self.write(
            os.path.join(self.home, "sessions", "0.json"),
            {"sessionId": "different-session", "status": "busy"},
        )
        self.write(
            os.path.join(self.home, "projects", "some-project", "different-session.jsonl"),
            user(99, None, "human"),
        )
        code, events, _ = self.run_events(handle, "--include-text")
        self.assertEqual(code, 0)
        self.assertTrue(any(event.get("report") == "first report" for event in events))
        self.assertTrue(all(event["session"] == handle for event in events))
        self.assertEqual(self.run_events("1")[0:2], (2, []))

        duplicate = os.path.join(self.home, "sessions", "2.json")
        self.write(duplicate, {"sessionId": CLI_ID, "status": "idle"})
        self.assertEqual(self.run_events(handle)[0:2], (2, []))
        os.remove(duplicate)
        os.remove(os.path.join(self.home, "sessions", "1.json"))
        code, events, err = self.run_events(handle, "--include-text")
        self.assertEqual((code, events), (2, []))
        self.assertNotIn(handle, err)

    def test_transcript_filenames_are_literal_and_paths_must_be_unique_and_contained(self) -> None:
        project = os.path.join(self.home, "projects", "some-project")
        original = os.path.join(project, f"{CLI_ID}.jsonl")
        literal = os.path.join(project, "[marker]*?.jsonl")
        self.assertIsNone(probe.transcript_path(self.home, "*"))
        self.write_raw(literal, "")
        self.assertEqual(probe.transcript_path(self.home, "[marker]*?"), os.path.realpath(literal))
        for invalid in ("", ".", "..", "../elsewhere", r"..\elsewhere", "null\0id"):
            self.assertIsNone(probe.transcript_path(self.home, invalid))

        second_project = os.path.join(self.home, "projects", "second-project")
        os.mkdir(second_project)
        second = os.path.join(second_project, f"{CLI_ID}.jsonl")
        self.write_raw(second, "")
        self.assertIsNone(probe.transcript_path(self.home, CLI_ID))
        os.remove(second)
        os.remove(original)
        outside = os.path.join(self.tmp.name, f"{CLI_ID}.jsonl")
        self.write_raw(outside, "")
        os.symlink(outside, original)
        self.assertIsNone(probe.transcript_path(self.home, CLI_ID))
        os.remove(original)
        os.symlink(literal, original)
        self.assertIsNone(probe.transcript_path(self.home, CLI_ID))

    def test_reader_rechecks_regular_transcript_after_lookup(self) -> None:
        path = probe.transcript_path(self.home, CLI_ID)
        assert path is not None
        outside = os.path.join(self.tmp.name, f"{CLI_ID}.jsonl")
        self.write(outside, {"text": "unrelated synthetic data"})
        os.remove(path)
        os.symlink(outside, path)
        self.assertEqual(list(probe.read_rows(path)), [])
        os.remove(path)
        os.mkfifo(path)
        real_open = os.open

        def open_nonblocking(name, flags, *args, **kwargs):
            if name == os.path.basename(path):
                self.assertTrue(flags & os.O_NONBLOCK)
            return real_open(name, flags, *args, **kwargs)

        with mock.patch.object(probe.os, "open", side_effect=open_nonblocking):
            self.assertEqual(list(probe.read_rows(path)), [])
        project = os.path.dirname(path)
        os.rename(project, f"{project}-old")
        os.symlink(self.tmp.name, project)
        self.assertEqual(list(probe.read_rows(path)), [])

    def test_events_suppresses_terminal_report_if_idle_observation_changes(self) -> None:
        entry = {"sessionId": CLI_ID, "status": "idle", "statusUpdatedAt": 123}
        handle = probe.survey(self.home, self.app)["sessions"][0]["session_handle"]
        read_rows = probe.read_rows

        def read(path):
            yield from read_rows(path)
            self.assertEqual(active_registry.call_count, 1)

        for refreshed in (
            [entry],
            [{**entry, "status": "busy"}],
            [{**entry, "statusUpdatedAt": 456}],
            [],
        ):
            with self.subTest(refreshed=refreshed):
                with (
                    mock.patch.object(probe, "read_rows", side_effect=read),
                    mock.patch.object(
                        probe, "registry", side_effect=[[entry], refreshed]
                    ) as active_registry,
                ):
                    code, events, _ = self.run_events(handle)
                self.assertEqual(active_registry.call_count, 2)
                self.assertEqual(code, 0)
                turn_reports = [
                    event["turn"]
                    for event in events
                    if "turn" in event and event["kind"] == "claude.turn_report"
                ]
                self.assertEqual(turn_reports, [1, 2, 3] if refreshed == [entry] else [1, 2])

    def test_watch_reports_only_real_growth_and_a_valid_status_age(self) -> None:
        def sample(size: int, inode: int = 2) -> os.stat_result:
            return os.stat_result((stat.S_IFREG, inode, 3, 1, 0, 0, size, 0, 0, 0))

        for samples, timestamp, grows, age in (
            ([sample(10), sample(8)], None, False, None),
            ([sample(10), FileNotFoundError()], "bad", False, None),
            ([FileNotFoundError(), sample(20)], True, False, None),
            ([sample(10), sample(10)], 0, False, None),
            ([sample(10), sample(20, inode=4)], 10_001, False, None),
            ([sample(10), sample(20)], 9_900, True, 100),
        ):
            with self.subTest(samples=samples, timestamp=timestamp):
                busy = {"sessionId": CLI_ID, "status": "busy"}
                idle = {**busy, "status": "idle"}
                if timestamp is not None:
                    idle["statusUpdatedAt"] = timestamp
                with (
                    mock.patch.object(probe, "registry", side_effect=[[busy], [idle]]),
                    mock.patch.object(probe, "transcript_path", return_value="synthetic"),
                    mock.patch.object(probe.os, "stat", side_effect=samples),
                    mock.patch.object(probe.time, "monotonic", side_effect=[0, 0, 1, 2]),
                    mock.patch.object(probe.time, "time", return_value=10),
                    mock.patch.object(probe.time, "sleep"),
                ):
                    (flip,) = probe.watch(self.home, 2)["busy_to_idle_flips"]
                self.assertEqual(flip["transcript_grew_since_last_poll"], grows)
                self.assertEqual(flip["registry_status_age_ms"], age)

    def test_watch_gap_breaks_observation_continuity(self) -> None:
        busy = {"sessionId": CLI_ID, "status": "busy"}
        idle = {**busy, "status": "idle"}
        with (
            mock.patch.object(probe, "registry", side_effect=[[busy], [], [idle]]),
            mock.patch.object(probe, "transcript_path", return_value=None),
            mock.patch.object(probe.time, "monotonic", side_effect=[0, 0, 1, 2, 3]),
            mock.patch.object(probe.time, "sleep"),
        ):
            self.assertEqual(probe.watch(self.home, 3)["busy_to_idle_flips"], [])


if __name__ == "__main__":
    unittest.main()
