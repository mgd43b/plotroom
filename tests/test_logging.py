import io
import json
import logging
from collections.abc import Iterator
from pathlib import Path

import pytest

from plotroom.app.logging import setup_logging


@pytest.fixture(autouse=True)
def restore_root_logger() -> Iterator[None]:
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    yield
    for added in root.handlers:
        if added not in handlers:
            added.close()
    root.handlers[:] = handlers
    root.setLevel(level)


def test_lines_are_json_with_extra_fields() -> None:
    stream = io.StringIO()
    setup_logging(stream)
    logging.getLogger("plotroom.test").info("hello", extra={"port": 8765})
    entry = json.loads(stream.getvalue())
    assert entry["msg"] == "hello"
    assert entry["level"] == "info"
    assert entry["logger"] == "plotroom.test"
    assert entry["port"] == 8765
    assert entry["ts"].endswith("Z")


def test_exceptions_are_one_line_of_json() -> None:
    stream = io.StringIO()
    setup_logging(stream)
    try:
        raise RuntimeError("boom")
    except RuntimeError:
        logging.getLogger("plotroom.test").exception("failed")
    lines = stream.getvalue().splitlines()
    assert len(lines) == 1
    assert "RuntimeError: boom" in json.loads(lines[0])["exc"]


def test_uvicorns_colored_copy_of_a_message_is_dropped() -> None:
    stream = io.StringIO()
    setup_logging(stream)
    logging.getLogger("uvicorn.error").info(
        "Started", extra={"color_message": "\x1b[32mStarted\x1b[0m"}
    )
    assert "color_message" not in json.loads(stream.getvalue())


def test_httpx_request_urls_are_not_logged_at_info() -> None:
    stream = io.StringIO()
    setup_logging(stream)
    logging.getLogger("httpx").info('HTTP Request: POST http://h/mcp?token=abc "200 OK"')
    logging.getLogger("httpcore.connection").info("connect to h")
    assert stream.getvalue() == ""


def log_lines(path: Path) -> list[dict[str, object]]:
    """Every line of ``path``, each parsed: a line that is not one JSON object fails the test."""
    entries: list[dict[str, object]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        entry = json.loads(line)
        assert isinstance(entry, dict)
        entries.append(entry)  # pyright: ignore[reportUnknownArgumentType]
    return entries


def write_numbered(count: int, *, start: int = 0) -> None:
    for number in range(start, start + count):
        logging.getLogger("plotroom.test").info("line %03d", number, extra={"n": number})


def test_a_file_gets_the_json_lines_and_the_stream_gets_none(tmp_path: Path) -> None:
    stream = io.StringIO()
    path = tmp_path / "not" / "yet" / "serve.log"
    setup_logging(stream, file=path)
    logging.getLogger("plotroom.test").info("héllo", extra={"port": 1})
    assert stream.getvalue() == ""
    [entry] = log_lines(path)
    assert entry["msg"] == "héllo"
    assert entry["port"] == 1


@pytest.mark.parametrize("backups", [1, 2, 3])
def test_rotation_keeps_exactly_the_configured_number_of_old_files(
    tmp_path: Path, backups: int
) -> None:
    path = tmp_path / "serve.log"
    limit = 300
    setup_logging(file=path, max_bytes=limit, backups=backups)
    write_numbered(60)  # a line is about 100 bytes: far more than backups + 1 files' worth
    names = sorted(entry.name for entry in tmp_path.iterdir())
    assert names == sorted(["serve.log", *(f"serve.log.{n}" for n in range(1, backups + 1))])
    for name in names:
        assert (tmp_path / name).stat().st_size <= limit
        log_lines(tmp_path / name)  # every line is one JSON object
    assert log_lines(path)[-1]["n"] == 59, "the newest line is in the active file"
    newest_old = log_lines(tmp_path / "serve.log.1")
    assert newest_old[-1]["n"] == int(str(log_lines(path)[0]["n"])) - 1, "no line was lost between"


def test_a_roll_with_no_backups_empties_the_active_file(tmp_path: Path) -> None:
    path = tmp_path / "serve.log"
    setup_logging(file=path, max_bytes=300, backups=0)
    write_numbered(60)
    assert [entry.name for entry in tmp_path.iterdir()] == ["serve.log"]
    assert path.stat().st_size <= 300
    assert log_lines(path)[-1]["n"] == 59


def test_a_line_longer_than_the_limit_is_written_whole(tmp_path: Path) -> None:
    path = tmp_path / "serve.log"
    setup_logging(file=path, max_bytes=50, backups=1)
    logging.getLogger("plotroom.test").info("x" * 200)
    [entry] = log_lines(path)
    assert entry["msg"] == "x" * 200


def test_an_unwritable_log_path_raises_and_leaves_the_loggers_alone(tmp_path: Path) -> None:
    blocker = tmp_path / "file"
    blocker.write_text("not a directory", encoding="utf-8")
    stream = io.StringIO()
    setup_logging(stream)
    before = list(logging.getLogger().handlers)
    with pytest.raises(OSError, match="file"):
        setup_logging(file=blocker / "serve.log")
    assert logging.getLogger().handlers == before
