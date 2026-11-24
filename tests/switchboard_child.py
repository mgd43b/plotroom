"""Scripted MCP relay using only a temporary directory for delivery state."""

import json
import os
import sys
import time
from pathlib import Path
from typing import Any


def publish(path: Path, value: str) -> None:
    pending = path.with_suffix(".tmp")
    pending.write_text(value)
    pending.replace(path)


def serve(state: Path) -> None:
    board_file = state / "board"
    board = board_file.read_text() if board_file.exists() else "fixture-board"
    bridge = (state / "bridge-fixture").exists()
    assert os.environ["SWITCHBOARD_BOARD"] == board
    assert os.environ["SWITCHBOARD_AUTO"] == "0"
    if not (state / "available").exists():
        (state / "unavailable").touch()
        return
    for line in sys.stdin:
        request: dict[str, Any] = json.loads(line)
        if "id" not in request:
            continue
        params = request.get("params", {})
        result: dict[str, Any]
        if request["method"] == "initialize":
            result = {
                "protocolVersion": params["protocolVersion"],
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "fixture-relay", "version": "1"},
            }
        elif request["method"] == "tools/list":
            result = {
                "tools": [
                    {"name": name, "inputSchema": {"type": "object"}}
                    for name in ("register", "wait", "ack", "send")
                ]
            }
        else:
            name, args = params["name"], params["arguments"]
            if name == "register":
                assert args == {"name": "plotroom"}
                payload = {"ok": True, "you": "plotroom"}
            elif name == "wait":
                receipt = "redelivered" if (state / "interrupted").exists() else "first"
                messages = (
                    []
                    if (state / "acked").exists()
                    else [
                        {
                            "id": 7,
                            "from": "fixture-peer",
                            "body": "worker message",
                            "receipt": receipt,
                        }
                    ]
                )
                if (
                    bridge
                    and (state / "acked").exists()
                    and (state / "outbound-cause").exists()
                    and not (state / "acked-cause").exists()
                ):
                    messages = [
                        {
                            "id": 8,
                            "from": "fixture-peer",
                            "body": "trigger the fixture send",
                            "receipt": "cause",
                        }
                    ]
                if not messages:
                    time.sleep(0.05)
                payload = {"messages": messages}
            elif name == "ack" and bridge and args["id"] == 8:
                assert args["receipt"] == "cause"
                publish(state / "acked-cause", args["receipt"])
                payload = {"ok": True, "id": 8}
            elif name == "ack":
                assert args["id"] == 7
                if args["receipt"] == "first":
                    (state / "interrupted").touch()
                    if bridge:
                        while os.getppid() != 1:
                            time.sleep(0.025)
                        return
                    time.sleep(60)
                    raise AssertionError("expected interruption before ack")
                assert args["receipt"] == "redelivered"
                publish(state / "acked", args["receipt"])
                payload = {"ok": True, "id": 7}
            elif name == "send":
                with (state / "send-requests.jsonl").open("a") as requests:
                    requests.write(json.dumps(args) + "\n")
                delivered = state / "send-deliveries.json"
                deliveries: dict[str, dict[str, Any]] = (
                    json.loads(delivered.read_text()) if delivered.exists() else {}
                )
                assert deliveries.setdefault(args["idempotency_key"], args) == args
                publish(delivered, json.dumps(deliveries))
                first = state / "send-first"
                if not first.exists():
                    publish(first, json.dumps(args))
                    if not bridge:
                        time.sleep(60)
                        raise AssertionError("expected interruption after send")
                else:
                    publish(state / "send-retry", json.dumps(args))
                payload = {"id": 11, "to": args["to"]}
            else:
                raise AssertionError(name)
            result = {"content": [], "structuredContent": payload}
        print(json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": result}), flush=True)


if __name__ == "__main__":
    serve(Path(sys.argv[1]))
