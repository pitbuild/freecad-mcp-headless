"""Stand-in for a FreeCAD worker: speaks the same protocol, needs no FreeCAD.

Used by the unit tests to exercise the bridge and the MCP surface deterministically.
Set ``FAKE_DELAY_MS`` to simulate a slow call and ``FAKE_EXIT_AFTER`` to simulate a crash.
"""

import json
import os
import socket
import time

port = int(os.environ["FREECAD_MCP_PORT"])
token = os.environ["FREECAD_MCP_TOKEN"]
delay_ms = int(os.environ.get("FAKE_DELAY_MS", "0"))
exit_after = int(os.environ.get("FAKE_EXIT_AFTER", "0"))

sock = socket.create_connection(("127.0.0.1", port), timeout=30)
stream = sock.makefile("rwb")


def send(payload):
    stream.write((json.dumps(payload) + "\n").encode())
    stream.flush()


send(
    {
        "event": "ready",
        "token": token,
        "protocol": 1,
        "pid": os.getpid(),
        "freecad": "0.0-test",
        "python": "3.test",
    }
)

calls = 0
while True:
    line = stream.readline()
    if not line:
        break
    request = json.loads(line)
    method = request.get("method")
    calls += 1
    if exit_after and calls >= exit_after:
        send({"id": request.get("id"), "ok": True, "result": {"closing": True}})
        break
    if delay_ms:
        time.sleep(delay_ms / 1000.0)
    if method == "shutdown":
        send({"id": request.get("id"), "ok": True, "result": {"bye": True}})
        break
    if method == "explode":
        send(
            {
                "id": request.get("id"),
                "ok": False,
                "error": {"kind": "worker_error", "message": "boom", "traceback": "tb"},
            }
        )
        continue
    send(
        {
            "id": request.get("id"),
            "ok": True,
            "result": {"method": method, "params": request.get("params") or {}, "calls": calls},
        }
    )
