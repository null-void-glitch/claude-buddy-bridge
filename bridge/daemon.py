"""Bridge daemon: owns the Cardputer's BLE (Nordic UART Service) connection,
tracks Claude Code session activity, and answers IPC requests from hook
scripts.

Wire protocol (device firmware, src/xfer.h / src/data.h / REFERENCE.md):
  host -> device heartbeat:
    {"total","running","waiting","msg","entries":[...],
     "tokens","tokens_today","completed","prompt":{"id","tool","hint"}}
    "completed" pulses true for exactly one heartbeat to trigger the
    firmware's celebrate animation (src/main.cpp derive() -> P_CELEBRATE);
    it reads back false on every subsequent beat.
  device -> host permission response:
    {"cmd":"permission","id":"...","decision":"once"|"deny"}
  simple commands (name/owner/species/unpair/status), each acked:
    {"cmd":"status"} -> {"ack":"status","ok":true,"data":{...}}
  character upload: char_begin -> file -> chunk* -> file_end -> char_end,
    each step acked before the next.
"""
import base64
import json
import logging
import os
import signal
import socketserver
import threading
import time
from datetime import date
from pathlib import Path

from . import ipc, transcript
from .ble_link import BLELink, find_device_sync

log = logging.getLogger("buddy.daemon")

HEARTBEAT_INTERVAL = 10.0
DEVICE_CMD_TIMEOUT = 5.0
PROMPT_WAIT_TIMEOUT = 280.0  # keep under the PreToolUse hook's own timeout
RAW_CHUNK_SIZE = 180  # base64(180) ~= 240 chars, decodes well under the
                       # firmware's 300-byte chunk buffer


def _short_hint(tool_name, tool_input):
    if isinstance(tool_input, dict):
        for key in ("command", "file_path", "path", "pattern", "url", "prompt"):
            if key in tool_input and tool_input[key]:
                return str(tool_input[key])[:60]
    return str(tool_input)[:60] if tool_input else tool_name


class Daemon:
    def __init__(self, address=None):
        self._lock = threading.RLock()
        self.sessions = {}  # session_id -> {"state", "transcript_path"}
        self.session_tokens = {}  # session_id -> cumulative tokens
        self.entries = []  # recent activity strings, newest first
        self.prompt_queue = []  # list of dict(id, tool, hint, event, result)
        self._pending_ack = None  # {"cmd", "event", "result"} for device_cmd
        self._today = date.today()
        self._tokens_today_base = 0
        self._pending_completed = False
        self._heartbeat_dirty = threading.Event()

        address = address or find_device_sync()
        log.info("connecting to Cardputer at %s over BLE", address)
        self.link = BLELink(address, self._on_device_line, on_reconnect=self._touch)
        self.link.start()

        self._hb_thread = threading.Thread(target=self._heartbeat_loop, daemon=True)
        self._hb_thread.start()

    # ---- device -> host -------------------------------------------------

    def _on_device_line(self, obj):
        with self._lock:
            if obj.get("cmd") == "permission":
                req_id = obj.get("id")
                decision = obj.get("decision")
                for entry in self.prompt_queue:
                    if entry["id"] == req_id:
                        entry["result"] = "deny" if decision == "deny" else "allow"
                        entry["event"].set()
                        self.prompt_queue.remove(entry)
                        break
                self._heartbeat_dirty.set()
                return
            if "ack" in obj and self._pending_ack and obj["ack"] == self._pending_ack["cmd"]:
                self._pending_ack["result"] = obj
                self._pending_ack["event"].set()
                return

    # ---- heartbeat --------------------------------------------------------

    def _rollover_day(self):
        today = date.today()
        if today != self._today:
            self._today = today
            self._tokens_today_base = sum(self.session_tokens.values())

    def _snapshot(self):
        with self._lock:
            self._rollover_day()
            total = len(self.sessions)
            running = sum(1 for s in self.sessions.values() if s["state"] == "running")
            waiting = len(self.prompt_queue)
            if self.prompt_queue:
                p = self.prompt_queue[0]
                msg = f"approve: {p['tool']}"
                prompt = {"id": p["id"], "tool": p["tool"], "hint": p["hint"]}
            else:
                msg = f"running ({running})" if running else "idle"
                prompt = None
            tokens = sum(self.session_tokens.values())
            tokens_today = max(0, tokens - self._tokens_today_base)
            completed, self._pending_completed = self._pending_completed, False
            return {
                "total": total,
                "running": running,
                "waiting": waiting,
                "msg": msg,
                "completed": completed,
                "entries": list(self.entries[:6]),
                "tokens": tokens,
                "tokens_today": tokens_today,
                **({"prompt": prompt} if prompt else {}),
            }

    def send_heartbeat(self):
        try:
            self.link.send(self._snapshot())
        except Exception:
            log.exception("failed to send heartbeat")

    def _heartbeat_loop(self):
        while True:
            self.send_heartbeat()
            self._heartbeat_dirty.wait(timeout=HEARTBEAT_INTERVAL)
            self._heartbeat_dirty.clear()

    def _touch(self):
        self._heartbeat_dirty.set()

    # ---- session bookkeeping ----------------------------------------------

    def _note_activity(self, text):
        self.entries.insert(0, text[:40])
        self.entries[:] = self.entries[:8]

    def _update_tokens(self, session_id, transcript_path):
        _, tokens = transcript.summarize(transcript_path)
        if tokens:
            self.session_tokens[session_id] = tokens

    # ---- IPC ops ------------------------------------------------------------

    def op_session_start(self, payload):
        with self._lock:
            self.sessions[payload["session_id"]] = {
                "state": "idle",
                "transcript_path": payload.get("transcript_path"),
            }
            self._touch()
        return {"ok": True}

    def op_session_end(self, payload):
        with self._lock:
            self.sessions.pop(payload["session_id"], None)
            self.session_tokens.pop(payload["session_id"], None)
            self._touch()
        return {"ok": True}

    def op_tool_pre(self, payload):
        session_id = payload["session_id"]
        tool_name = payload.get("tool_name", "tool")
        hint = _short_hint(tool_name, payload.get("tool_input"))
        transcript_path = payload.get("transcript_path")

        with self._lock:
            self.sessions.setdefault(session_id, {"state": "idle"})
            self.sessions[session_id]["state"] = "waiting"
            if transcript_path:
                self.sessions[session_id]["transcript_path"] = transcript_path
                self._update_tokens(session_id, transcript_path)
            req_id = f"req_{session_id[:8]}_{int(time.time() * 1000) % 1000000}"
            entry = {
                "id": req_id,
                "tool": tool_name,
                "hint": hint,
                "event": threading.Event(),
                "result": None,
            }
            self.prompt_queue.append(entry)
            self._touch()

        resolved = entry["event"].wait(timeout=PROMPT_WAIT_TIMEOUT)

        with self._lock:
            if entry in self.prompt_queue:
                self.prompt_queue.remove(entry)
            self.sessions.setdefault(session_id, {"state": "idle"})["state"] = "running"
            self._touch()

        if not resolved:
            return {"decision": "ask", "reason": "buddy: no response from device"}
        if entry["result"] == "deny":
            return {"decision": "deny", "reason": "denied on Claude Buddy"}
        return {"decision": "allow"}

    def op_tool_post(self, payload):
        session_id = payload["session_id"]
        tool_name = payload.get("tool_name", "tool")
        with self._lock:
            self.sessions.setdefault(session_id, {"state": "idle"})["state"] = "idle"
            transcript_path = payload.get("transcript_path")
            if transcript_path:
                self._update_tokens(session_id, transcript_path)
            self._note_activity(tool_name)
            self._touch()
        return {"ok": True}

    def op_note(self, payload):
        with self._lock:
            self._note_activity(payload.get("text", ""))
            if payload.get("celebrate"):
                self._pending_completed = True
            self._touch()
        return {"ok": True}

    def op_device_cmd(self, payload):
        cmd = payload["cmd"]
        args = payload.get("args") or {}
        with self._lock:
            event = threading.Event()
            self._pending_ack = {"cmd": cmd, "event": event, "result": None}
            self.link.send({"cmd": cmd, **args})
        resolved = event.wait(timeout=DEVICE_CMD_TIMEOUT)
        with self._lock:
            result = self._pending_ack["result"]
            self._pending_ack = None
        if not resolved:
            return {"ok": False, "error": "device did not respond"}
        return result

    def op_push_character(self, payload):
        return self._push_character(payload["dir"])

    def _device_call(self, obj, ack_name, timeout=DEVICE_CMD_TIMEOUT):
        with self._lock:
            event = threading.Event()
            self._pending_ack = {"cmd": ack_name, "event": event, "result": None}
            self.link.send(obj)
        resolved = event.wait(timeout=timeout)
        with self._lock:
            result = self._pending_ack["result"]
            self._pending_ack = None
        if not resolved:
            raise RuntimeError(f"device did not ack '{ack_name}' in time")
        if not result.get("ok"):
            raise RuntimeError(f"device rejected '{ack_name}': {result.get('error')}")
        return result

    def _push_character(self, dir_path):
        root = Path(dir_path)
        manifest = root / "manifest.json"
        if not manifest.exists():
            return {"ok": False, "error": f"no manifest.json in {dir_path}"}
        try:
            name = json.loads(manifest.read_text()).get("name", root.name)
        except json.JSONDecodeError as e:
            return {"ok": False, "error": f"bad manifest.json: {e}"}

        files = [p for p in root.rglob("*") if p.is_file()]
        total = sum(p.stat().st_size for p in files)

        try:
            self._device_call({"cmd": "char_begin", "name": name, "total": total}, "char_begin")
            for p in files:
                rel = str(p.relative_to(root))
                size = p.stat().st_size
                self._device_call({"cmd": "file", "path": rel, "size": size}, "file")
                written = 0
                with open(p, "rb") as f:
                    while True:
                        chunk = f.read(RAW_CHUNK_SIZE)
                        if not chunk:
                            break
                        b64 = base64.b64encode(chunk).decode("ascii")
                        self._device_call({"cmd": "chunk", "d": b64}, "chunk")
                        written += len(chunk)
                self._device_call({"cmd": "file_end"}, "file_end")
                log.info("pushed %s (%d bytes)", rel, written)
            self._device_call({"cmd": "char_end"}, "char_end")
        except RuntimeError as e:
            return {"ok": False, "error": str(e)}
        return {"ok": True, "name": name, "files": len(files), "bytes": total}


class _Handler(socketserver.StreamRequestHandler):
    OPS = {
        "session_start": "op_session_start",
        "session_end": "op_session_end",
        "tool_pre": "op_tool_pre",
        "tool_post": "op_tool_post",
        "note": "op_note",
        "device_cmd": "op_device_cmd",
        "push_character": "op_push_character",
    }

    def handle(self):
        line = self.rfile.readline()
        if not line:
            return
        try:
            msg = json.loads(line.decode("utf-8", "replace"))
        except json.JSONDecodeError:
            self.wfile.write(b'{"ok":false,"error":"bad json"}\n')
            return
        op = msg.pop("op", None)
        if op == "ping":
            self.wfile.write(b'{"ok":true}\n')
            return
        method_name = self.OPS.get(op)
        if not method_name:
            self.wfile.write(json.dumps({"ok": False, "error": f"unknown op {op}"}).encode() + b"\n")
            return
        try:
            result = getattr(self.server.daemon, method_name)(msg)
        except Exception as e:
            log.exception("op %s failed", op)
            result = {"ok": False, "error": str(e)}
        self.wfile.write(json.dumps(result).encode("utf-8") + b"\n")


class _Server(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True

    def __init__(self, sock_path, daemon):
        if os.path.exists(sock_path):
            os.remove(sock_path)
        super().__init__(sock_path, _Handler)
        self.daemon = daemon


def run(address=None, sock_path=None):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    sock_path = sock_path or ipc.SOCK_PATH
    d = Daemon(address=address)
    server = _Server(sock_path, d)
    log.info("listening on %s", sock_path)

    def _handle_signal(signum, _frame):
        log.info("received signal %s, shutting down", signum)
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    try:
        server.serve_forever()
    finally:
        server.shutdown()
        os.remove(sock_path)
        d.link.stop()
