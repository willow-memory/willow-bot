"""Unix socket client for deterministic delegate ops."""
from __future__ import annotations

import json
import socket

from willow_bot.deterministic.policy import Policy


def client_op(policy: Policy, req: dict, *, timeout_s: float = 60.0) -> dict:
    payload = (json.dumps(req, sort_keys=True) + "\n").encode("utf-8")
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(timeout_s)
    sock.connect(str(policy.socket_path))
    sock.sendall(payload)
    buf = b""
    while b"\n" not in buf:
        chunk = sock.recv(65536)
        if not chunk:
            break
        buf += chunk
    sock.close()
    line = buf.split(b"\n", 1)[0].decode("utf-8")
    return json.loads(line)
