"""Local IPC between Claude Code hook scripts and the bridge daemon.

One request = one JSON line sent over a Unix domain socket, one JSON line
back. Kept deliberately simple: hooks are short-lived processes that make
a single call and exit.
"""
import json
import os
import socket

SOCK_PATH = os.environ.get(
    "BUDDY_SOCK", os.path.expanduser("~/.claude-buddy-bridge.sock")
)


def request(op, payload=None, timeout=5.0):
    """Sends {"op": op, **payload} to the daemon and returns its response
    dict, or None if the daemon is unreachable or the call times out.

    Callers (hooks) must treat None as "daemon not available" and fail
    open rather than blocking Claude Code.
    """
    msg = {"op": op, **(payload or {})}
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(timeout)
            s.connect(SOCK_PATH)
            s.sendall((json.dumps(msg) + "\n").encode("utf-8"))
            s.shutdown(socket.SHUT_WR)
            chunks = []
            while True:
                chunk = s.recv(4096)
                if not chunk:
                    break
                chunks.append(chunk)
            data = b"".join(chunks).strip()
            if not data:
                return None
            return json.loads(data.decode("utf-8", "replace"))
    except (OSError, socket.timeout, json.JSONDecodeError):
        return None
