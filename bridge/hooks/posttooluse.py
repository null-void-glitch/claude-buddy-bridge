#!/usr/bin/env python3
"""Claude Code PostToolUse hook: reports the finished tool call to the
bridge daemon so the Cardputer's activity feed and token counters update.
Best-effort; failures are silent so a dead daemon never affects tool use.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from bridge import ipc  # noqa: E402


def main():
    payload = json.load(sys.stdin)
    ipc.request(
        "tool_post",
        {
            "session_id": payload.get("session_id"),
            "tool_name": payload.get("tool_name"),
            "transcript_path": payload.get("transcript_path"),
        },
        timeout=3,
    )
    sys.exit(0)


if __name__ == "__main__":
    main()
