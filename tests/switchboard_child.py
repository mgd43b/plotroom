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
    assert os.environ["SWITCHBOARD_BOARD"] == "fixture-board"
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
                if not messages:
                    time.sleep(0.05)
                payload = {"messages": messages}
            elif name == "ack":
                assert args["id"] == 7
                if args["receipt"] == "first":
                    (state / "interrupted").touch()
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
                    time.sleep(60)
                    raise AssertionError("expected interruption after send")
                publish(state / "send-retry", json.dumps(args))
                payload = {"id": 11, "to": args["to"]}
            else:
                raise AssertionError(name)
            result = {"content": [], "structuredContent": payload}
        print(json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": result}), flush=True)


if __name__ == "__main__":
    serve(Path(sys.argv[1]))
