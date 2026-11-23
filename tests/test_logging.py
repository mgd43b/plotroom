import io
import json
import logging
from collections.abc import Iterator

import pytest

from plotroom.app.logging import setup_logging


@pytest.fixture(autouse=True)
def restore_root_logger() -> Iterator[None]:
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    yield
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
