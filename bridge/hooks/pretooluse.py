#!/usr/bin/env python3
"""Claude Code PreToolUse hook: asks the bridge daemon (and therefore the
Cardputer's Y/N keys) whether to allow the pending tool call.

If the daemon isn't running or doesn't answer in time, this fails open
(permissionDecision "ask") so Claude Code falls back to its normal
interactive permission prompt instead of hanging.

Bash commands containing a standalone `sudo` are additionally rewritten to
run under `sudo -A` with SUDO_ASKPASS pointed at sudo_askpass.py. The Bash
tool's subprocess stdio is piped for output capture and shares no usable
terminal with the running Claude Code session (its own UI holds that tty in
raw mode), so plain `sudo` has no interactive path to prompt for a
password; the askpass helper pops a GUI password dialog instead, at the
moment sudo actually needs it, so the password itself never passes through
this hook, the daemon, or the Cardputer — only the Cardputer's Y/N approval
for the command does.
"""
import base64
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from bridge import ipc  # noqa: E402

SUDO_WORD_RE = re.compile(r"(?<!\S)sudo(?!\S)")
ASKPASS_PATH = str(Path(__file__).resolve().parent / "sudo_askpass.py")


def _rewrite_sudo(command):
    """Rewrites each standalone `sudo` in *command* to run via the askpass
    helper. Assumes *command* already matched SUDO_WORD_RE."""
    cmd_b64 = base64.b64encode(command.encode("utf-8")).decode("ascii")
    replacement = f"CLAUDE_SUDO_CMD_B64={cmd_b64} SUDO_ASKPASS={ASKPASS_PATH} sudo -A"
    return SUDO_WORD_RE.sub(replacement, command)


def main():
    payload = json.load(sys.stdin)
    tool_name = payload.get("tool_name")
    tool_input = payload.get("tool_input") or {}
    command = tool_input.get("command") if tool_name == "Bash" else None
    needs_sudo_gate = bool(command) and SUDO_WORD_RE.search(command)

    resp = ipc.request(
        "tool_pre",
        {
            "session_id": payload.get("session_id"),
            "tool_name": tool_name,
            "tool_input": tool_input,
            "transcript_path": payload.get("transcript_path"),
        },
        timeout=290,
    )
    decision = (resp or {}).get("decision", "ask")
    out = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": decision,
        }
    }
    if resp and resp.get("reason"):
        out["hookSpecificOutput"]["permissionDecisionReason"] = resp["reason"]
    if needs_sudo_gate and decision != "deny":
        out["hookSpecificOutput"]["updatedInput"] = {
            **tool_input,
            "command": _rewrite_sudo(command),
        }
    print(json.dumps(out))
    sys.exit(0)


if __name__ == "__main__":
    main()
