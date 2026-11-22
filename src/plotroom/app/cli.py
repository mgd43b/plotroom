"""The ``plotroom`` command line."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from plotroom.app.config import ConfigError, load_config
from plotroom.app.lock import LockError
from plotroom.app.logging import setup_logging

EXIT_FAILURE = 1
EXIT_CONFIG = 2
EXIT_LOCKED = 3

log = logging.getLogger("plotroom")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="plotroom")
    commands = parser.add_subparsers(dest="command", required=True)

    serve = commands.add_parser("serve", help="own a data directory and serve the tools over HTTP")
    serve.add_argument("--config", type=Path, help="TOML config file (default: PLOTROOM_CONFIG)")

    connect = commands.add_parser("connect", help="reach a running server")
    connect.add_argument("--stdio", action="store_true", required=True, help="proxy stdio to HTTP")
    connect.add_argument("--url", help="the server's MCP endpoint (default: from the config)")
    connect.add_argument("--config", type=Path, help="TOML config file (default: PLOTROOM_CONFIG)")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "serve":
        setup_logging(sys.stdout)
        return _serve(args.config)
    setup_logging(sys.stderr)  # stdout is the protocol channel
    return _connect(args.url, args.config)


def _serve(config_path: Path | None) -> int:
    from plotroom.app.serve import Daemon

    try:
        config = load_config(config_path, os.environ)
    except ConfigError as exc:
        log.error("invalid configuration", extra={"reason": str(exc)})
        return EXIT_CONFIG
    try:
        asyncio.run(Daemon(config).run())
    except LockError as exc:
        log.error("data directory is locked", extra={"reason": str(exc)})
        return EXIT_LOCKED
    except KeyboardInterrupt:
        pass
    except Exception:
        log.exception("serve failed")
        return EXIT_FAILURE
    return 0


def _connect(url: str | None, config_path: Path | None) -> int:
    from plotroom.adapters.mcp.proxy import StdioProxy

    target = url or os.environ.get("PLOTROOM_URL")
    if target is None:
        try:
            target = load_config(config_path, os.environ, require_projects=False).url
        except ConfigError as exc:
            log.error("invalid configuration", extra={"reason": str(exc)})
            return EXIT_CONFIG
    with contextlib.suppress(KeyboardInterrupt, BrokenPipeError):
        asyncio.run(StdioProxy(target).run())
    return 0
