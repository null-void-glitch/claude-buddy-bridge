"""Best-effort extraction of recent activity and token usage from a Claude
Code transcript JSONL file.

The transcript format is internal to Claude Code and can change between
releases, so every read here is advisory: failures are swallowed and
callers fall back to zeros/empty strings rather than raising.
"""
import json

_MAX_TAIL_BYTES = 65536
_MAX_LINES = 40


def _tail_lines(path):
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - _MAX_TAIL_BYTES))
            data = f.read()
    except OSError:
        return []
    lines = data.decode("utf-8", "replace").splitlines()
    return lines[-_MAX_LINES:]


def summarize(path):
    """Returns (activity_line, cumulative_tokens) from the tail of the
    transcript, or (None, 0) if nothing usable is found."""
    if not path:
        return None, 0
    activity = None
    tokens = 0
    for line in reversed(_tail_lines(path)):
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(entry, dict):
            continue
        msg = entry.get("message")
        if not isinstance(msg, dict):
            continue
        usage = msg.get("usage")
        if isinstance(usage, dict) and not tokens:
            tokens = (usage.get("input_tokens") or 0) + (usage.get("output_tokens") or 0)
        if activity is None:
            content = msg.get("content")
            if isinstance(content, list):
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    if block.get("type") == "text" and block.get("text"):
                        activity = block["text"].strip().splitlines()[0][:40]
                        break
                    if block.get("type") == "tool_use":
                        activity = block.get("name", "tool")
                        break
        if activity is not None and tokens:
            break
    return activity, tokens
