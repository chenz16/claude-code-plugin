#!/usr/bin/env python3
"""
Local WebSocket server for Claude Code remote control.
Private, zero-latency alternative to Telegram bot.

Access from phone browser: http://<your-ip>:8080
Features: real-time chat, voice recording, tmux control.

Usage:
    python -m remote.web_server
    python -m remote.web_server --port 8080 --host 0.0.0.0
"""

import asyncio
import json
import logging
import os
import re
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from shared.tmux_utils import find_claude_instances, capture_pane, send_to_pane
from shared.config import CAPTURE_LINES
from shared.ssh_remote import test_ssh, list_remote_sessions, send_to_remote_tmux

logging.basicConfig(format="%(asctime)s [%(levelname)s] %(message)s", level=logging.INFO)
log = logging.getLogger(__name__)

# Focused terminal state
_focused_target = None
_focused_project = None
_focused_host = None  # None = local, "user@host" = remote
_focused_last_activity = 0
FOCUS_TIMEOUT = 1800  # auto-unfocus after 30 minutes of inactivity

# Remote hosts discovered via SSH
_remote_hosts = {}  # ip -> user@host


def _scan_ssh_hosts():
    """Discover active SSH connections."""
    hosts = {}
    try:
        import subprocess
        ret = subprocess.run(["pgrep", "-a", "ssh"],
                             capture_output=True, text=True, timeout=3)
        for line in ret.stdout.strip().splitlines():
            parts = line.split()
            for part in parts[1:]:
                if "@" in part and not part.startswith("-") and "/" not in part and not part.startswith("["):
                    ip = part.split("@")[-1]
                    if ip and not ip.startswith("-"):
                        hosts[ip] = part
    except Exception:
        pass
    return hosts


def _remote_capture_pane(host, session, lines=40):
    """Capture tmux pane output on a remote host via SSH (one-shot)."""
    import subprocess
    try:
        ret = subprocess.run(
            ["ssh", host, f"tmux capture-pane -t {session} -p -S -{lines}"],
            capture_output=True, text=True, timeout=10,
        )
        return ret.stdout
    except Exception:
        return ""


def _remote_send_to_pane(host, session, text, press_enter=True):
    """Send text to a remote tmux pane via SSH."""
    import subprocess
    escaped = text.replace("\\", "\\\\").replace("'", "'\\''").replace(";", "\\;")
    enter_part = " Enter" if press_enter else ""
    cmd = f"ssh {host} \"tmux send-keys -t {session} '{escaped}'{enter_part}\""
    subprocess.run(cmd, shell=True, capture_output=True, timeout=10)


class RemoteStreamWatcher:
    """Persistent SSH connection that continuously captures tmux output.

    Instead of polling with a new SSH call each time, keeps one SSH
    connection open running a capture loop on the remote side.
    Output changes are detected and pushed via callback.
    """

    def __init__(self, host, session, on_output, interval=0.3):
        self.host = host
        self.session = session
        self.on_output = on_output  # async callback(text)
        self.interval = interval
        self._proc = None
        self._task = None
        self._running = False
        self._last_content = ""

    async def start(self):
        """Start the persistent SSH stream."""
        self._running = True
        # Capture current content as baseline so we don't replay history
        self._last_content = _remote_capture_pane(self.host, self.session, 20)
        self._task = asyncio.create_task(self._run())

    async def stop(self):
        """Stop the stream and kill SSH process."""
        self._running = False
        if self._proc:
            try:
                self._proc.terminate()
                self._proc = None
            except Exception:
                pass
        if self._task:
            self._task.cancel()
            self._task = None

    async def _run(self):
        """Main loop: one persistent SSH connection, remote-side capture loop."""
        import subprocess

        # Remote command: loop capturing tmux pane, separated by markers
        remote_cmd = (
            f"while true; do "
            f"tmux capture-pane -t {self.session} -p -S -20 2>/dev/null; "
            f"echo '@@__FRAME__@@'; "
            f"sleep {self.interval}; "
            f"done"
        )

        try:
            self._proc = subprocess.Popen(
                ["ssh", self.host, remote_cmd],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
            )

            buffer = []
            loop = asyncio.get_event_loop()

            while self._running and self._proc and self._proc.poll() is None:
                # Read line by line in a thread to not block event loop
                line = await loop.run_in_executor(None, self._proc.stdout.readline)
                if not line:
                    break

                if line.strip() == "@@__FRAME__@@":
                    # Got a complete frame
                    frame = "\n".join(buffer)
                    buffer = []

                    if frame != self._last_content and frame.strip():
                        # Extract new Claude Code reply blocks
                        from collections import Counter
                        old = self._last_content.splitlines()
                        new = frame.splitlines()

                        # Find truly new lines (Counter handles duplicates)
                        old_counter = Counter(old)
                        new_lines = []
                        for l in new:
                            if old_counter.get(l, 0) > 0:
                                old_counter[l] -= 1
                            else:
                                new_lines.append(l)

                        # Extract reply blocks: ● line + continuation lines
                        # Skip prompts (❯), separators (───), UI noise
                        reply_parts = []
                        in_reply = False
                        for l in new_lines:
                            stripped = l.strip()
                            if not stripped:
                                if in_reply:
                                    reply_parts.append('')
                                continue
                            if stripped.startswith('●'):
                                in_reply = True
                                reply_parts.append(stripped[1:].strip())
                            elif in_reply:
                                # Continuation: indented lines, list items, etc.
                                if stripped.startswith('❯') or stripped.startswith('⏵') or all(c in '─━═' for c in stripped):
                                    in_reply = False
                                elif 'bypass permissions' in stripped.lower():
                                    in_reply = False
                                else:
                                    reply_parts.append(stripped)

                        if reply_parts:
                            reply_text = "\n".join(reply_parts).strip()
                            if reply_text:
                                try:
                                    await self.on_output(reply_text[-2000:])
                                except Exception:
                                    pass
                        self._last_content = frame
                else:
                    buffer.append(line.rstrip("\n"))

        except asyncio.CancelledError:
            pass
        except Exception as e:
            log.error("StreamWatcher error: %s", e)
        finally:
            if self._proc:
                try:
                    self._proc.terminate()
                except Exception:
                    pass
                self._proc = None


# Active stream watcher (one per focused session)
_active_watcher = None


def _local_paste(text):
    """Send text to the most recently active local Claude Code terminal.
    Finds Claude Code processes, checks their pts mtime, writes to the most recent one."""
    import subprocess
    import fcntl
    import termios

    # Find all claude processes and their pts
    ret = subprocess.run(
        ["ps", "-eo", "pid,tty,args", "--no-headers"],
        capture_output=True, text=True, timeout=3,
    )
    best_time = 0
    best_pts = None
    for line in ret.stdout.strip().splitlines():
        parts = line.split(None, 2)
        if len(parts) < 3:
            continue
        pid, tty, cmd = parts
        if "claude" not in cmd.lower() or tty == "?" or "web_server" in cmd or "voice" in cmd or "screenshot" in cmd:
            continue
        if not tty.startswith("pts/"):
            continue
        pts_path = f"/dev/{tty}"
        try:
            mtime = os.stat(pts_path).st_mtime
            if mtime > best_time:
                best_time = mtime
                best_pts = pts_path
        except OSError:
            continue

    if not best_pts:
        log.warning("No local Claude Code terminal found")
        return

    # Write text to the pts using TIOCSTI (terminal input simulation)
    try:
        with open(best_pts, 'w') as fd:
            for char in text:
                fcntl.ioctl(fd, termios.TIOCSTI, char.encode())
        log.info("Local paste to %s: %s", best_pts, text[:50])
    except PermissionError:
        # TIOCSTI might be disabled, fall back to xdotool
        log.warning("TIOCSTI failed, falling back to xdotool")
        subprocess.run(["xclip", "-selection", "clipboard"], input=text.encode(), timeout=5)
        import time as _t; _t.sleep(0.05)
        subprocess.run(["xdotool", "key", "--clearmodifiers", "ctrl+shift+v"], timeout=5)


def clean_terminal_output(text):
    """Clean terminal output for mobile display."""
    # Remove ANSI escape codes
    text = re.sub(r'\x1b\[[0-9;]*[a-zA-Z]', '', text)
    text = re.sub(r'\x1b\][^\x07]*\x07', '', text)
    # Remove box-drawing characters and decorative lines
    text = re.sub(r'[─━┌┐└┘├┤┬┴┼╔╗╚╝╠╣╦╩╬║═│┃▀▄█▌▐░▒▓╭╮╰╯]+', '', text)
    # Remove lines that are only dashes, equals, underscores, dots, or spaces
    lines = text.splitlines()
    cleaned = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        # Skip pure separator lines
        if all(c in '-=_~.*#>' for c in stripped):
            continue
        # Skip spinner/progress lines
        if '██' in stripped or '▓▓' in stripped or '░░' in stripped:
            continue
        # Skip Claude Code UI elements
        if 'bypass permissions' in stripped.lower():
            continue
        if 'shift+tab to cycle' in stripped.lower():
            continue
        if stripped.startswith('print(') or stripped.startswith('❯') or stripped == '❯':
            continue
        if stripped in ('⏵⏵', '⏵', '›'):
            continue
        # Skip spinner/loading lines (e.g. "* Tinkering...", "+ Zesting...")
        if '...' in stripped and len(stripped) < 30:
            # Short line ending with ... and starting with a non-letter = spinner
            if stripped[0] not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789':
                continue
        cleaned.append(stripped)
    return "\n".join(cleaned)


def get_instances_info():
    """Get list of active Claude Code sessions (local + remote)."""
    instances = []

    # Local tmux sessions
    for inst in find_claude_instances():
        instances.append({
            "project": inst["project"],
            "cwd": inst["cwd"],
            "target": inst["target"],
            "host": None,  # local
        })

    # Remote tmux sessions
    for ip, host_str in _remote_hosts.items():
        try:
            sessions = list_remote_sessions(host_str)
            for sess in sessions:
                instances.append({
                    "project": f"{sess}@{ip}",
                    "cwd": host_str,
                    "target": sess,
                    "host": host_str,
                })
        except Exception:
            pass

    # Add "Local Paste" option at the end
    instances.append({
        "project": "Local Paste",
        "cwd": "paste to focused window",
        "target": "__local_paste__",
        "host": "__local__",
    })

    # Add index
    for i, inst in enumerate(instances):
        inst["idx"] = i + 1

    return instances


async def handle_message(msg, send_fn):
    """Process incoming message and send responses via send_fn."""
    global _focused_target, _focused_project, _focused_host, _focused_last_activity

    msg_type = msg.get("type", "text")
    text = msg.get("text", "").strip()

    # Command handling
    if text.startswith("/"):
        parts = text.split(None, 2)
        cmd = parts[0].lower()

        if cmd == "/list":
            instances = get_instances_info()
            if not instances:
                await send_fn({"type": "text", "text": "No active Claude Code sessions."})
            else:
                lines = []
                for inst in instances:
                    if inst.get("host"):
                        output = _remote_capture_pane(inst["host"], inst["target"], 3)
                    else:
                        output = capture_pane(inst["target"], 3)
                    cleaned = clean_terminal_output(output)
                    last = cleaned.splitlines()[-1][:60] if cleaned else "(empty)"
                    lines.append(f"#{inst['idx']} {inst['project']}\n  {last}")
                await send_fn({"type": "text", "text": "\n\n".join(lines)})
            return

        elif cmd == "/focus":
            global _active_watcher
            instances = get_instances_info()
            if not instances:
                await send_fn({"type": "text", "text": "No active sessions."})
                return
            try:
                idx = int(parts[1]) - 1
                inst = instances[idx]
            except (IndexError, ValueError):
                await send_fn({"type": "text", "text": f"Usage: /focus <1-{len(instances)}>"})
                return

            # Stop previous watcher
            if _active_watcher:
                await _active_watcher.stop()
                _active_watcher = None

            _focused_target = inst["target"]
            _focused_project = inst["project"]
            _focused_host = inst["host"]
            _focused_last_activity = time.time()

            if _focused_host and _focused_host != "__local__":
                output = _remote_capture_pane(_focused_host, _focused_target, 5)
            elif not _focused_host:
                output = capture_pane(_focused_target, 5)
            else:
                output = ""
            cleaned = clean_terminal_output(output)
            lines = cleaned.splitlines()[-3:] if cleaned else []
            await send_fn({"type": "text", "text": f"Focused on [{_focused_project}]\n" + "\n".join(lines)})

            # Start persistent stream watcher for remote sessions
            if _focused_host and _focused_host != "__local__":
                _active_watcher = RemoteStreamWatcher(
                    _focused_host, _focused_target,
                    on_output=lambda text: send_fn({"type": "text", "text": text}),
                    interval=0.3,
                )
                await _active_watcher.start()
                log.info("Started stream watcher for %s:%s", _focused_host, _focused_target)
            return

        elif cmd == "/unfocus":
            if _active_watcher:
                await _active_watcher.stop()
                _active_watcher = None
            _focused_target = None
            _focused_project = None
            _focused_host = None
            await send_fn({"type": "text", "text": "Unfocused."})
            return

        elif cmd == "/peek":
            target = _focused_target
            host = _focused_host
            if len(parts) > 1:
                instances = get_instances_info()
                try:
                    idx = int(parts[1]) - 1
                    target = instances[idx]["target"]
                    host = instances[idx]["host"]
                except (IndexError, ValueError):
                    pass
            if not target:
                await send_fn({"type": "text", "text": "No terminal. Use /focus first or /peek <n>"})
                return
            if host:
                output = _remote_capture_pane(host, target, CAPTURE_LINES)
            else:
                output = capture_pane(target, CAPTURE_LINES)
            await send_fn({"type": "text", "text": clean_terminal_output(output)[-3000:]})
            return

        elif cmd == "/send":
            instances = get_instances_info()
            try:
                idx = int(parts[1]) - 1
                inst = instances[idx]
                command = parts[2] if len(parts) > 2 else ""
                if not command:
                    raise ValueError
            except (IndexError, ValueError):
                await send_fn({"type": "text", "text": "Usage: /send <n> <text>"})
                return
            if inst["host"]:
                _remote_send_to_pane(inst["host"], inst["target"], command)
                await asyncio.sleep(2)
                output = _remote_capture_pane(inst["host"], inst["target"], 10)
            else:
                send_to_pane(inst["target"], command)
                await asyncio.sleep(2)
                output = capture_pane(inst["target"], 10)
            await send_fn({"type": "text", "text": clean_terminal_output(output)[-1500:]})
            return

    # Handle voice audio
    if msg_type == "audio":
        audio_data = msg.get("data")  # base64 encoded
        if not audio_data:
            await send_fn({"type": "text", "text": "No audio data."})
            return

        import base64
        import subprocess as _sp3
        audio_bytes = base64.b64decode(audio_data)

        # Save as webm (browser format), convert to wav for SenseVoice
        with tempfile.NamedTemporaryFile(suffix=".webm", delete=False) as tmp:
            tmp.write(audio_bytes)
            webm_path = tmp.name

        wav_path = webm_path.replace(".webm", ".wav")
        try:
            _sp3.run(["ffmpeg", "-y", "-i", webm_path, "-ar", "16000", "-ac", "1", wav_path],
                     capture_output=True, timeout=10)
            from shared.transcribe import transcribe_file
            text = transcribe_file(wav_path)
        finally:
            for p in [webm_path, wav_path]:
                if os.path.exists(p):
                    os.unlink(p)

        if not text:
            await send_fn({"type": "text", "text": "No speech detected."})
            return

        log.info("Voice: %s", text)
        # Show recognized text on phone as user message
        await send_fn({"type": "voice_text", "text": text})
        # Process as text (recursive)
        await handle_message({"type": "text", "text": text}, send_fn)
        return

    # Regular text in focused mode
    if _focused_target:
        # Check timeout
        if time.time() - _focused_last_activity > FOCUS_TIMEOUT:
            _focused_target = None
            _focused_project = None
            _focused_host = None
            await send_fn({"type": "text", "text": "Auto-unfocused (idle > 30min)."})
            return

        _focused_last_activity = time.time()
        if _focused_host == "__local__":
            _local_paste(text)
            await send_fn({"type": "text", "text": "已粘贴到本地窗口"})
            return
        elif _focused_host:
            _remote_send_to_pane(_focused_host, _focused_target, f"[M] {text}")
        else:
            send_to_pane(_focused_target, f"[M] {text}")
        await send_fn({"type": "waiting", "text": "等待回复..."})
        # Stream watcher handles output automatically
        return

    # No focus — just show status
    instances = get_instances_info()
    if not instances:
        await send_fn({"type": "text", "text": "No active sessions. Use /list"})
    else:
        await send_fn({"type": "text", "text": f"{len(instances)} session(s). Use /focus <n> to connect."})


# ── HTML UI ──

HTML_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="default">
<meta name="mobile-web-app-capable" content="yes">
<meta name="theme-color" content="#075E54">
<link rel="manifest" href="/manifest.json">
<title>Claude Code</title>
<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: -apple-system, system-ui, sans-serif; background: #ededed; color: #111; height: 100vh; display: flex; flex-direction: column; }

/* ===== Sessions page (friends list) ===== */
#page-sessions { display: flex; flex-direction: column; height: 100vh; }
.topbar { background: #075E54; color: white; padding: 14px 16px; font-size: 18px; font-weight: 600; display: flex; align-items: center; justify-content: space-between; }
.topbar .status { font-size: 11px; font-weight: 400; opacity: 0.8; }
#session-list { flex: 1; overflow-y: auto; background: white; }
.s-item { display: flex; align-items: center; padding: 14px 16px; border-bottom: 1px solid #f0f0f0; cursor: pointer; }
.s-item:active { background: #f5f5f5; }
.s-avatar { width: 50px; height: 50px; border-radius: 50%; background: #25D366; display: flex; align-items: center; justify-content: center; font-size: 20px; color: white; font-weight: 700; flex-shrink: 0; margin-right: 14px; }
.s-avatar.local { background: #128C7E; }
.s-info { flex: 1; min-width: 0; }
.s-name { font-size: 16px; font-weight: 600; color: #111; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.s-host { font-size: 13px; color: #667; margin-top: 2px; }

/* ===== Chat page ===== */
#page-chat { display: none; flex-direction: column; height: 100vh; }
.chat-topbar { background: #075E54; color: white; padding: 10px 16px; display: flex; align-items: center; gap: 12px; }
.back-btn { font-size: 22px; cursor: pointer; padding: 4px 8px; }
.chat-name { font-size: 16px; font-weight: 600; flex: 1; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
#messages { flex: 1; overflow-y: auto; padding: 8px 12px; background: #e5ddd5; }
.msg { margin-bottom: 6px; padding: 7px 10px; border-radius: 8px; max-width: 82%; word-wrap: break-word; white-space: pre-wrap; font-size: 14px; line-height: 1.4; position: relative; clear: both; }
.msg.user { background: #DCF8C6; color: #111; float: right; border-bottom-right-radius: 2px; }
.msg.bot { background: white; color: #111; float: left; border-bottom-left-radius: 2px; }
.msg.waiting { background: white; color: #999; float: left; font-style: italic; }
.msg::after { content: ''; display: block; clear: both; }
#input-area { background: #f0f0f0; padding: 6px 8px; display: flex; gap: 6px; align-items: center; }
#text-input { flex: 1; padding: 10px 14px; border-radius: 24px; border: none; background: white; color: #111; font-size: 15px; outline: none; }
.input-btn { border: none; cursor: pointer; border-radius: 50%; width: 46px; height: 46px; display: flex; align-items: center; justify-content: center; font-size: 20px; -webkit-tap-highlight-color: transparent; }
#send-btn { background: #075E54; color: white; }
#mic-btn { background: #075E54; color: white; }
#mic-btn.recording { background: #e53935; animation: pulse 1s infinite; }
@keyframes pulse { 0%,100% { opacity: 1; } 50% { opacity: 0.5; } }
.clearfix::after { content: ''; display: block; clear: both; }
</style>
</head>
<body>

<!-- Sessions page (friends list) -->
<div id="page-sessions">
  <div class="topbar">
    <div>Claude Code<div class="status" id="status">connecting...</div></div>
  </div>
  <div id="session-list"></div>
</div>

<!-- Chat page -->
<div id="page-chat">
  <div class="chat-topbar">
    <div class="back-btn" onclick="goBack()">&#8592;</div>
    <div class="chat-name" id="chat-name"></div>
  </div>
  <div id="messages"></div>
  <div id="input-area">
    <div id="mic-btn" class="input-btn" ontouchstart="event.preventDefault();startMic()" ontouchend="event.preventDefault();stopMic()" onmousedown="startMic()" onmouseup="stopMic()">&#127908;</div>
    <input id="text-input" placeholder="Type a message" onkeydown="if(event.key==='Enter')sendText()">
    <div id="send-btn" class="input-btn" ontouchstart="event.preventDefault();sendText()" onclick="sendText()">&#10148;</div>
  </div>
</div>

<script>
let ws, mediaRecorder, audioChunks = [], isRecording = false;
let focusedIdx = null, focusedName = '';
// Per-session chat history: { idx: [{who, text}] }
const chatHistory = {};

function connect() {
  const proto = (location.protocol === 'https:') ? 'wss:' : 'ws:';
  ws = new WebSocket(proto + '//' + location.host + '/ws');
  ws.onopen = () => {
    document.getElementById('status').textContent = 'online';
    loadSessions();
  };
  ws.onclose = () => {
    document.getElementById('status').textContent = 'offline';
    setTimeout(connect, 2000);
  };
  ws.onmessage = (e) => {
    const msg = JSON.parse(e.data);
    if (!focusedIdx) return;
    if (msg.type === 'voice_text') {
      const v = document.getElementById('voice-placeholder');
      if (v) { v.textContent = msg.text; v.removeAttribute('id'); }
      else { addMsg(msg.text, 'user'); }
      saveChat(focusedIdx, 'user', msg.text);
    } else if (msg.type === 'waiting') {
      const div = document.createElement('div');
      div.className = 'msg waiting clearfix';
      div.textContent = msg.text;
      div.id = 'waiting-msg';
      document.getElementById('messages').appendChild(div);
      div.scrollIntoView({behavior:'smooth'});
    } else {
      const w = document.getElementById('waiting-msg');
      if (w) w.remove();
      if (msg.text && !msg.text.startsWith('Focused on')) {
        addMsg(msg.text, 'bot');
        saveChat(focusedIdx, 'bot', msg.text);
      }
    }
  };
}

function saveChat(idx, who, text) {
  if (!chatHistory[idx]) chatHistory[idx] = [];
  chatHistory[idx].push({who, text});
}

function addMsg(text, who) {
  const div = document.createElement('div');
  div.className = 'msg ' + who + ' clearfix';
  div.textContent = text;
  document.getElementById('messages').appendChild(div);
  div.scrollIntoView({behavior:'smooth'});
}

function renderChat(idx) {
  const msgs = document.getElementById('messages');
  msgs.innerHTML = '';
  (chatHistory[idx] || []).forEach(m => addMsg(m.text, m.who));
}

// === Sessions page ===
async function loadSessions() {
  const list = document.getElementById('session-list');
  list.innerHTML = '<div style="padding:20px;color:#999;text-align:center">Loading...</div>';
  try {
    const resp = await fetch('/api/sessions');
    const sessions = await resp.json();
    if (!sessions.length) {
      list.innerHTML = '<div style="padding:20px;color:#999;text-align:center">No sessions</div>';
      return;
    }
    list.innerHTML = sessions.map(s => {
      const isLocal = s.host === '__local__';
      const initial = isLocal ? 'L' : s.project.charAt(0).toUpperCase();
      const hostLabel = isLocal ? 'Local paste' : (s.host || 'local tmux');
      const unread = (chatHistory[s.idx] || []).filter(m => m.who === 'bot').length;
      return '<div class="s-item" onclick="openChat(' + s.idx + ',\\x27' + s.project.replace(/'/g,'') + '\\x27)">' +
        '<div class="s-avatar' + (isLocal ? ' local' : '') + '">' + initial + '</div>' +
        '<div class="s-info"><div class="s-name">' + s.project + '</div>' +
        '<div class="s-host">' + hostLabel + '</div></div></div>';
    }).join('');
  } catch(e) {
    list.innerHTML = '<div style="padding:20px;color:red;text-align:center">Failed to load</div>';
  }
}

function openChat(idx, name) {
  if (!ws || ws.readyState !== WebSocket.OPEN) return;
  focusedIdx = idx;
  focusedName = name;
  document.getElementById('chat-name').textContent = name;
  document.getElementById('page-sessions').style.display = 'none';
  document.getElementById('page-chat').style.display = 'flex';
  renderChat(idx);
  ws.send(JSON.stringify({type:'text', text:'/focus ' + idx}));
  document.getElementById('text-input').focus();
}

function goBack() {
  document.getElementById('page-chat').style.display = 'none';
  document.getElementById('page-sessions').style.display = 'flex';
  focusedIdx = null;
  ws.send(JSON.stringify({type:'text', text:'/unfocus'}));
  loadSessions();
}

function sendText() {
  const input = document.getElementById('text-input');
  const text = input.value.trim();
  if (!text || !focusedIdx) return;
  if (!ws || ws.readyState !== WebSocket.OPEN) { connect(); return; }
  addMsg(text, 'user');
  saveChat(focusedIdx, 'user', text);
  ws.send(JSON.stringify({type:'text', text:text}));
  input.value = '';
  input.focus();
}

// === Voice ===
let micStream = null;
async function startMic() {
  if (isRecording || !focusedIdx) return;
  try {
    micStream = await navigator.mediaDevices.getUserMedia({audio:{sampleRate:16000,channelCount:1}});
    mediaRecorder = new MediaRecorder(micStream, {mimeType:'audio/webm'});
    audioChunks = [];
    mediaRecorder.ondataavailable = e => audioChunks.push(e.data);
    mediaRecorder.onstop = async () => {
      micStream.getTracks().forEach(t=>t.stop()); micStream=null;
      const blob = new Blob(audioChunks, {type:'audio/webm'});
      const reader = new FileReader();
      reader.onload = () => {
        const b64 = reader.result.split(',')[1];
        const div = document.createElement('div');
        div.className = 'msg user clearfix'; div.textContent = '识别中...';
        div.id = 'voice-placeholder';
        document.getElementById('messages').appendChild(div);
        div.scrollIntoView({behavior:'smooth'});
        ws.send(JSON.stringify({type:'audio',data:b64}));
      };
      reader.readAsDataURL(blob);
    };
    mediaRecorder.start(); isRecording = true;
    document.getElementById('mic-btn').classList.add('recording');
  } catch(err) { addMsg('Mic: '+err.message,'bot'); }
}

function stopMic() {
  if (!isRecording) return;
  isRecording = false;
  document.getElementById('mic-btn').classList.remove('recording');
  if (mediaRecorder && mediaRecorder.state !== 'inactive') mediaRecorder.stop();
}

connect();
</script>
</body>
</html>"""


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Claude Code local web remote")
    parser.add_argument("--host", default="0.0.0.0", help="Bind address (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=8080, help="Port (default: 8080)")
    parser.add_argument("--no-ssl", action="store_true", help="Disable HTTPS (for local debugging)")
    args = parser.parse_args()

    try:
        from fastapi import FastAPI, WebSocket, WebSocketDisconnect
        from fastapi.responses import HTMLResponse
        import uvicorn
    except ImportError:
        print("ERROR: FastAPI not installed.")
        print("  pip install fastapi uvicorn")
        sys.exit(1)

    app = FastAPI()

    @app.get("/")
    async def index():
        return HTMLResponse(HTML_PAGE)

    @app.get("/api/sessions")
    async def api_sessions():
        from fastapi.responses import JSONResponse
        instances = get_instances_info()
        return JSONResponse([{
            "idx": inst["idx"],
            "project": inst["project"],
            "host": inst.get("host"),
        } for inst in instances])

    @app.get("/manifest.json")
    async def manifest():
        from fastapi.responses import JSONResponse
        return JSONResponse({
            "name": "Claude Code Remote",
            "short_name": "Claude",
            "start_url": "/",
            "display": "standalone",
            "background_color": "#1a1a2e",
            "theme_color": "#1a1a2e",
            "description": "Voice & text remote control for Claude Code",
        })

    @app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket):
        await websocket.accept()
        log.info("Client connected: %s", websocket.client)
        state = {"connected": True}

        async def send_fn(msg):
            if state["connected"]:
                try:
                    await websocket.send_json(msg)
                    log.info("Sent to client: %s", str(msg.get("text", ""))[:80])
                except Exception as e:
                    log.error("Send failed: %s", e)
                    state["connected"] = False

        try:
            while True:
                data = await websocket.receive_text()
                msg = json.loads(data)
                log.info("Received: %s", str(msg)[:100])
                try:
                    await handle_message(msg, send_fn)
                except Exception as e:
                    log.error("Error: %s", e, exc_info=True)
                    await send_fn({"type": "text", "text": f"Error: {e}"})
        except WebSocketDisconnect:
            state["connected"] = False
            # Stop stream watcher on disconnect
            global _active_watcher
            if _active_watcher:
                await _active_watcher.stop()
                _active_watcher = None
            log.info("Client disconnected")

    # Get the right IP for phone access
    import socket
    import subprocess as _sp
    local_ip = "localhost"

    # Check if running in WSL — need Windows host IP, not WSL internal IP
    is_wsl = False
    try:
        with open("/proc/version", "r") as f:
            is_wsl = "microsoft" in f.read().lower()
    except Exception:
        pass

    if is_wsl:
        # Get Windows LAN IP (accessible from phone)
        try:
            ret = _sp.run(
                ["powershell.exe", "-Command",
                 "Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.InterfaceAlias -notmatch 'Loopback|vEthernet|WSL' -and $_.IPAddress -notmatch '^169\\.' } | Select -First 1 -ExpandProperty IPAddress"],
                capture_output=True, text=True, timeout=10,
            )
            win_ip = ret.stdout.strip().split("\n")[0].strip()
            if win_ip:
                local_ip = win_ip
                # Auto-setup port forwarding (WSL IP -> Windows)
                wsl_ip = "localhost"
                try:
                    s2 = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                    s2.connect(("8.8.8.8", 80))
                    wsl_ip = s2.getsockname()[0]
                    s2.close()
                except Exception:
                    pass
                _sp.run(
                    ["powershell.exe", "-Command",
                     f"netsh interface portproxy delete v4tov4 listenport={args.port} listenaddress=0.0.0.0 2>$null; "
                     f"netsh interface portproxy add v4tov4 listenport={args.port} listenaddress=0.0.0.0 "
                     f"connectport={args.port} connectaddress={wsl_ip}"],
                    capture_output=True, timeout=10,
                )
        except Exception:
            pass
    else:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            local_ip = s.getsockname()[0]
            s.close()
        except Exception:
            pass

    # Auto-discover remote SSH hosts
    global _remote_hosts
    _remote_hosts = _scan_ssh_hosts()
    for ip, host_str in list(_remote_hosts.items()):
        if test_ssh(host_str):
            sessions = list_remote_sessions(host_str)
            log.info("Remote %s: %s", host_str, ", ".join(sessions) if sessions else "no sessions")
        else:
            del _remote_hosts[ip]
            log.warning("Remote %s: SSH failed, skipping", host_str)

    url = f"https://{local_ip}:{args.port}"
    print("")
    print("=" * 50)
    print("  Claude Code Remote (Local Web)")
    print(f"  URL: {url}")
    print("")

    # Show QR code in terminal
    try:
        import qrcode
        qr = qrcode.QRCode(border=1)
        qr.add_data(url)
        qr.make()
        qr.print_ascii(invert=True)
    except ImportError:
        print("  (install qrcode for QR: pip install qrcode)")

    print("")
    print("  Scan QR or open URL on phone.")
    print("  Add to home screen for app-like experience.")
    print("=" * 50)
    print("", flush=True)

    # Generate self-signed cert for HTTPS (required for mobile mic access)
    cert_dir = os.path.join(os.path.dirname(__file__), ".certs")
    cert_file = os.path.join(cert_dir, "cert.pem")
    key_file = os.path.join(cert_dir, "key.pem")

    # Regenerate cert if IP changed or cert doesn't exist
    regen = not os.path.exists(cert_file)
    if os.path.exists(cert_file):
        # Check if cert matches current IP
        import subprocess as _sp2
        ret = _sp2.run(["openssl", "x509", "-in", cert_file, "-text", "-noout"],
                       capture_output=True, text=True, timeout=5)
        if local_ip not in ret.stdout:
            regen = True

    if regen:
        os.makedirs(cert_dir, exist_ok=True)
        log.info("Generating self-signed certificate for HTTPS...")
        import subprocess as _sp2
        _sp2.run([
            "openssl", "req", "-x509", "-newkey", "rsa:2048",
            "-keyout", key_file, "-out", cert_file,
            "-days", "365", "-nodes",
            "-subj", f"/CN={local_ip}",
            "-addext", f"subjectAltName=IP:{local_ip},IP:127.0.0.1,DNS:localhost",
        ], capture_output=True)

    # Pre-load SenseVoice model for faster first voice request
    try:
        from shared.transcribe import load_model
        load_model()
    except Exception as e:
        log.warning("Failed to pre-load voice model: %s", e)

    if args.no_ssl:
        print(f"\n  Running in HTTP mode (no SSL)")
        print("", flush=True)
        uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    else:
        print(f"\n  NOTE: First time, accept the security warning in browser")
        print("", flush=True)
        uvicorn.run(app, host=args.host, port=args.port, log_level="warning",
                    ssl_certfile=cert_file, ssl_keyfile=key_file)


if __name__ == "__main__":
    main()
