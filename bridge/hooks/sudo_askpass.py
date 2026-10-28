#!/usr/bin/env python3
"""SUDO_ASKPASS helper invoked by `sudo -A` for sudo commands that the
pretooluse hook rewrote (see pretooluse.py:_rewrite_sudo).

sudo runs this as a separate program and reads the password from its
stdout. A terminal-based prompt (/dev/tty, or even the pty device directly)
doesn't work here: Claude Code's own UI holds that same terminal in raw
mode and reads it live for its own chat input, so a subprocess prompting
on it collides with the running session instead of getting a clean prompt
(confirmed — it visibly corrupted the chat input box on the first attempt).

A GUI password dialog sidesteps that entirely: it's a separate window, not
competing for the same tty. zenity is used since it's desktop-agnostic;
falls back to kdialog if zenity isn't installed.
"""
import base64
import os
import shutil
import subprocess
import sys


def _dialog_command(prompt_text):
    if shutil.which("zenity"):
        return ["zenity", "--password", "--title=claude-buddy", "--text", prompt_text]
    if shutil.which("kdialog"):
        return ["kdialog", "--password", prompt_text, "--title", "claude-buddy"]
    return None


def main():
    cmd_b64 = os.environ.get("CLAUDE_SUDO_CMD_B64", "")
    try:
        cmd = base64.b64decode(cmd_b64).decode("utf-8", "replace") if cmd_b64 else "a command"
    except (ValueError, UnicodeDecodeError):
        cmd = "a command"

    dialog_cmd = _dialog_command(f"Claude wants to run:\n{cmd}\n\nsudo password:")
    if dialog_cmd is None:
        sys.exit(1)

    try:
        result = subprocess.run(dialog_cmd, capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired):
        sys.exit(1)

    if result.returncode != 0:
        sys.exit(1)  # user cancelled the dialog

    password = result.stdout.rstrip("\n")
    if not password:
        sys.exit(1)
    print(password)


if __name__ == "__main__":
    main()
