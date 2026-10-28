#!/usr/bin/env python3
"""Claude Code SessionEnd hook: unregisters this session from the bridge
daemon. Kept fast since SessionEnd hooks share a tight timeout budget."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from bridge import ipc  # noqa: E402


def main():
    payload = json.load(sys.stdin)
    ipc.request(
        "session_end",
        {"session_id": payload.get("session_id")},
        timeout=1,
    )
    sys.exit(0)


if __name__ == "__main__":
    main()
