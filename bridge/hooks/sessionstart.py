#!/usr/bin/env python3
"""Claude Code SessionStart hook: registers this session with the bridge
daemon so it's counted in the Cardputer's session totals."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from bridge import ipc  # noqa: E402


def main():
    payload = json.load(sys.stdin)
    ipc.request(
        "session_start",
        {
            "session_id": payload.get("session_id"),
            "transcript_path": payload.get("transcript_path"),
        },
        timeout=3,
    )
    sys.exit(0)


if __name__ == "__main__":
    main()
