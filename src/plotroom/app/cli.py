"""The ``plotroom`` command line."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import os
import sys
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path

from plotroom.app.config import Config, ConfigError, load_config
from plotroom.app.lock import LockError
from plotroom.app.logging import setup_logging

EXIT_FAILURE = 1
EXIT_CONFIG = 2
EXIT_LOCKED = 4  # not 3: uvicorn exits 3 when it cannot start, e.g. when its port is taken

log = logging.getLogger("plotroom")


DEFAULT_START_TIMEOUT = 20.0


def _positive_seconds(text: str) -> float:
    try:
        seconds = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{text!r} is not a number of seconds") from None
    if not 0 < seconds < float("inf"):
        raise argparse.ArgumentTypeError("must be a positive number of seconds")
    return seconds


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="plotroom")
    commands = parser.add_subparsers(dest="command", required=True)

    serve = commands.add_parser("serve", help="own a data directory and serve the tools over HTTP")
    serve.add_argument("--config", type=Path, help="TOML config file (default: PLOTROOM_CONFIG)")
    serve.add_argument(
        "--log-file",
        type=Path,
        metavar="PATH",
        help="write the log to this size-rotating file instead of stdout",
    )

    connect = commands.add_parser("connect", help="reach a running server")
    connect.add_argument("--stdio", action="store_true", required=True, help="proxy stdio to HTTP")
    connect.add_argument("--url", help="the server's MCP endpoint (default: from the config)")
    connect.add_argument("--config", type=Path, help="TOML config file (default: PLOTROOM_CONFIG)")
    connect.add_argument(
        "--start-timeout",
        type=_positive_seconds,
        default=DEFAULT_START_TIMEOUT,
        metavar="SECONDS",
        help="how long to wait for a daemon started on demand (default: %(default)s)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "serve":
        return _serve(args.config, args.log_file)
    setup_logging(sys.stderr)  # stdout is the protocol channel
    return _connect(args.url, args.config, args.start_timeout)


def _serve(config_path: Path | None, log_file: Path | None) -> int:
    from plotroom.app.serve import Daemon

    # The limits come from the config, so it is read before logging is set up; until then, and when
    # it cannot be read, errors go to a plain stream handler (stderr is the daemon's own file).
    setup_logging(sys.stdout if log_file is None else sys.stderr)
    try:
        config = load_config(config_path, os.environ)
    except ConfigError as exc:
        log.error("invalid configuration", extra={"reason": str(exc)})
        return EXIT_CONFIG
    if log_file is not None:
        try:
            setup_logging(file=log_file, max_bytes=config.log_max_bytes, backups=config.log_backups)
        except OSError as exc:
            log.error("cannot open the log file", extra={"path": str(log_file), "reason": str(exc)})
            return EXIT_FAILURE
    try:
        asyncio.run(Daemon(config).run())
    except LockError as exc:
        # The winner owns the log file: a loser must not roll or delete it through its own handler.
        setup_logging(sys.stderr)
        log.error("data directory is locked", extra={"reason": str(exc)})
        return EXIT_LOCKED
    except KeyboardInterrupt:
        pass
    except Exception:
        log.exception("serve failed")
        return EXIT_FAILURE
    return 0


def _connect(url: str | None, config_path: Path | None, start_timeout: float) -> int:
    from plotroom.adapters.mcp.proxy import EndpointError, StdioProxy

    target = url or os.environ.get("PLOTROOM_URL")
    ensure: Callable[[], Awaitable[str | None]] | None = None
    if target is None:
        try:
            config = load_config(config_path, os.environ, require_projects=False)
        except ConfigError as exc:
            log.error("invalid configuration", extra={"reason": str(exc)})
            return EXIT_CONFIG
        target = config.url
        # Only a local server this config describes is started for the client; an explicit URL
        # names a server someone else runs.
        ensure = _starter(config, config_path, start_timeout)
    try:
        proxy = StdioProxy(target, ensure=ensure)
    except EndpointError as exc:
        log.error("invalid endpoint", extra={"reason": str(exc)})
        return EXIT_CONFIG
    with contextlib.suppress(KeyboardInterrupt, BrokenPipeError):
        asyncio.run(proxy.run())
    return 0


def _starter(
    config: Config, config_path: Path | None, timeout: float
) -> Callable[[], Awaitable[str | None]]:
    from plotroom.app.daemon_start import ensure_daemon  # keeps `serve` from loading the proxy

    async def start() -> str | None:
        return await ensure_daemon(config, config_path, env=os.environ, wait=timeout)

    return start
