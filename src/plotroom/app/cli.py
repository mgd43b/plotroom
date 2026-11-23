"""The ``plotroom`` command line."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import os
import sys
from collections.abc import Sequence
from functools import partial
from pathlib import Path

from plotroom.app.config import ConfigError, load_config
from plotroom.app.lock import LockError
from plotroom.app.logging import setup_logging
from plotroom.app.tokens import TokenError, TokenRegistry, select_local_token

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
    connect.add_argument("--token-id", help="local token ID (default: PLOTROOM_TOKEN_ID)")
    connect.add_argument(
        "--start-timeout",
        type=_positive_seconds,
        default=DEFAULT_START_TIMEOUT,
        metavar="SECONDS",
        help="how long to wait for a daemon started on demand (default: %(default)s)",
    )
    token = commands.add_parser("token", help="manage credentials without a running daemon")
    token_commands = token.add_subparsers(dest="action", required=True)
    issue = token_commands.add_parser("issue", help="issue a token and print it once as JSON")
    issue.add_argument("--project", required=True, help="project ID from the server config")
    issue.add_argument(
        "--role", required=True, help="orchestrator, clerk, worker:<name>, drain, bridge"
    )
    revoke = token_commands.add_parser("revoke", help="revoke a token by its public ID")
    revoke.add_argument("token_id", help="the public ID returned at issue")
    for action in (issue, revoke):
        action.add_argument(
            "--config", type=Path, help="TOML config file (default: PLOTROOM_CONFIG)"
        )
        action.add_argument(
            "--no-local", action="store_true", help="skip the local credentials file"
        )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "serve":
        return _serve(args.config, args.log_file)
    setup_logging(sys.stderr)  # stdout is the protocol channel
    if args.command == "token":
        return _token(args)
    return _connect(args.url, args.config, args.start_timeout, args.token_id)


def _token(args: argparse.Namespace) -> int:
    try:
        issuing = args.action == "issue"
        config = load_config(args.config, os.environ, require_projects=issuing)
        registry = TokenRegistry(config.data_dir)
        if issuing:
            if args.project not in config.projects:
                raise TokenError("the selected project is not configured")
            try:
                issued = registry.issue(args.project, args.role, local=not args.no_local)
            except ValueError:
                raise TokenError(
                    "token role must be orchestrator, clerk, worker:<name>, drain or bridge"
                ) from None
            print(json.dumps({"token_id": issued.token_id, "token": issued.bearer}))
        else:
            registry.revoke(args.token_id, local=not args.no_local)
    except (ConfigError, TokenError) as exc:
        log.error("cannot manage token", extra={"reason": str(exc)})
        return EXIT_CONFIG
    except OSError as exc:
        log.error("cannot write token files", extra={"reason": exc.strerror})
        return EXIT_FAILURE
    return 0


def _serve(config_path: Path | None, log_file: Path | None) -> int:
    from plotroom.app.portfile import START_DIRECTORY_ENV
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
        daemon = Daemon(config, expected_directory=os.environ.get(START_DIRECTORY_ENV))
        asyncio.run(daemon.run_as_service())
    except ConfigError as exc:
        log.error("invalid configuration", extra={"reason": str(exc)})
        return EXIT_CONFIG
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


def _connect(
    url: str | None, config_path: Path | None, start_timeout: float, token_id: str | None
) -> int:
    from plotroom.adapters.mcp.proxy import EndpointError, StdioProxy, UpstreamStartError
    from plotroom.app.daemon_start import DaemonLocation, ensure_daemon

    env = os.environ.copy()
    target = url or env.get("PLOTROOM_URL")
    bearer = env.get("PLOTROOM_TOKEN")
    token_id = token_id or env.get("PLOTROOM_TOKEN_ID")
    try:
        if token_id is not None and (target is not None or bearer is not None):
            raise TokenError("--token-id is for locally stored credentials and config-derived URLs")
        if target is not None:
            proxy = StdioProxy(target, bearer=bearer)
        else:
            if config_path is not None:
                config_path = config_path.resolve()
            elif path := env.get("PLOTROOM_CONFIG"):
                env["PLOTROOM_CONFIG"] = str(Path(path).expanduser().resolve())
            config = load_config(config_path, env, require_projects=False)
            location = DaemonLocation(config)
            if bearer is None:
                bearer = select_local_token(config.data_dir, token_id)
                if bearer is not None and location.identity is None:
                    raise TokenError("the local token's data directory no longer exists")
            proxy = StdioProxy(
                config.url,
                ensure=partial(ensure_daemon, location, config_path, env=env, wait=start_timeout),
                bearer=bearer,
                current_endpoint=location.current_endpoint,
            )
    except (ConfigError, EndpointError, TokenError, UpstreamStartError, OSError) as exc:
        log.error("invalid connection configuration", extra={"reason": str(exc)})
        return EXIT_CONFIG
    with contextlib.suppress(KeyboardInterrupt, BrokenPipeError):
        asyncio.run(proxy.run())
    return 0
