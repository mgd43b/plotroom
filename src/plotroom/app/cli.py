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

from plotroom.app.clerk_import import run_import
from plotroom.app.config import ConfigError, load_config
from plotroom.app.lock import LockError
from plotroom.app.logging import setup_logging
from plotroom.app.tokens import LocalCleanupError, TokenError, TokenRegistry, select_local_token

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
    connect.add_argument("--existing-local", action="store_true", help=argparse.SUPPRESS)
    command = commands.add_parser("import", help="import a clerk template into the local daemon")
    command.add_argument("--config", type=Path, help="TOML config file (default: PLOTROOM_CONFIG)")
    command.add_argument(
        "--token-id", help="local orchestrator token ID (default: PLOTROOM_TOKEN_ID)"
    )
    command.add_argument("--triggers", type=Path, help="copy of the clerk triggers.md file")
    command.add_argument("--ledger", type=Path, help="copy of the clerk ledger.md file")
    watch = commands.add_parser("watch", help="wait until the next visible board change")
    watch.add_argument("--config", type=Path, help="TOML config file (default: PLOTROOM_CONFIG)")
    watch.add_argument("--token-id", help="local token ID (default: PLOTROOM_TOKEN_ID)")
    watch.add_argument("--url", help="MCP endpoint (default: the local daemon)")
    watch.add_argument("--cursor", type=int, help="resume after this board sequence")
    bridge = commands.add_parser("bridge", help="relay local Switchboard mail to an MCP server")
    bridge.add_argument("--url", required=True, help="server MCP endpoint")
    bridge.add_argument("--board", required=True, help="the configured Switchboard board")
    bridge.add_argument("--mode", choices=("auto", "legacy"), default="auto")
    bridge.add_argument("--relay-command", nargs=argparse.REMAINDER, default=("switchboard-relay",))
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
    if args.command == "watch":
        return _watch(args.url, args.config, args.token_id, args.cursor)
    if args.command == "bridge":
        from plotroom.app.bridge import run_bridge

        return run_bridge(args.url, args.board, args.mode, args.relay_command)
    if args.command == "import":
        return run_import(args.config, args.token_id, args.triggers, args.ledger)
    return _connect(
        args.url, args.config, args.start_timeout, args.token_id, existing_local=args.existing_local
    )


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
    except LocalCleanupError as exc:
        invalid = isinstance(exc.failure, TokenError)
        reason = str(exc.failure) if invalid else "cannot update the local token store"
        log.error(
            "%s",
            exc,
            extra={
                "reason": reason,
                "hint": "retry the same revoke command after fixing the local store",
            },
        )
        return EXIT_CONFIG if invalid else EXIT_FAILURE
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


def _watch(url: str | None, config: Path | None, token_id: str | None, cursor: int | None) -> int:
    from mcp.client.client import Client
    from mcp.client.stdio import StdioServerParameters
    from mcp_types import TextContent

    args = ["-m", "plotroom", "connect", "--stdio"]
    for flag, value in (("--url", url), ("--config", config), ("--token-id", token_id)):
        if value is not None:
            args.extend((flag, str(value)))

    async def watch() -> int:
        position = cursor
        proxy = StdioServerParameters(command=sys.executable, args=args, env=os.environ.copy())
        async with Client(proxy, mode="legacy") as client:
            while True:
                response = await client.call_tool(
                    "wait", {} if position is None else {"cursor": position}
                )
                first = next(iter(response.content), None)
                if not isinstance(first, TextContent):
                    raise ValueError("wait returned no JSON result")
                result = json.loads(first.text)
                if not result["ok"]:
                    log.error("wait failed", extra={"reason": result["error"]["message"]})
                    return EXIT_FAILURE
                data = result["data"]
                position = int(data["cursor"])
                if data["item"] is not None:
                    print(json.dumps(data))
                    return 0

    try:
        return asyncio.run(watch())
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        log.error("watch connection failed", extra={"reason": type(exc).__name__})
        return EXIT_FAILURE


def _connect(
    url: str | None,
    config_path: Path | None,
    start_timeout: float,
    token_id: str | None,
    *,
    existing_local: bool = False,
) -> int:
    from plotroom.adapters.mcp.proxy import EndpointError, StdioProxy, UpstreamStartError
    from plotroom.app.daemon_start import DaemonLocation, ensure_daemon, require_daemon

    env = os.environ.copy()
    target = url or env.get("PLOTROOM_URL")
    bearer = env.get("PLOTROOM_TOKEN")
    token_id = token_id or env.get("PLOTROOM_TOKEN_ID")
    try:
        if existing_local and (target is not None or bearer is not None):
            raise TokenError("this connection requires a locally stored token and daemon")
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
            if existing_local and bearer is None:
                raise TokenError("no local credential is available for import")
            ensure = (
                partial(require_daemon, location, within=start_timeout)
                if existing_local
                else partial(ensure_daemon, location, config_path, env=env, wait=start_timeout)
            )
            proxy = StdioProxy(
                config.url,
                ensure=ensure,
                bearer=bearer,
                current_endpoint=location.current_endpoint,
            )
    except (ConfigError, EndpointError, TokenError, UpstreamStartError, OSError) as exc:
        log.error("invalid connection configuration", extra={"reason": str(exc)})
        return EXIT_CONFIG
    with contextlib.suppress(KeyboardInterrupt, BrokenPipeError):
        asyncio.run(proxy.run())
    return 0
