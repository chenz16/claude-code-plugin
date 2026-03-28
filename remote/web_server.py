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

logging.basicConfig(format="%(asctime)s [%(levelname)s] %(message)s", level=logging.INFO)
log = logging.getLogger(__name__)

# Focused terminal state
_focused_target = None
_focused_project = None
_focused_last_activity = 0
FOCUS_TIMEOUT = 1800  # auto-unfocus after 30 minutes of inactivity


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
        cleaned.append(stripped)
    return "\n".join(cleaned)


def get_instances_info():
    """Get list of active Claude Code sessions."""
    instances = find_claude_instances()
    return [{"idx": i + 1, "project": inst["project"], "cwd": inst["cwd"], "target": inst["target"]}
            for i, inst in enumerate(instances)]


async def handle_message(msg, send_fn):
    """Process incoming message and send responses via send_fn."""
    global _focused_target, _focused_project, _focused_last_activity

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
                    output = capture_pane(inst["target"], 3)
                    last = output.strip().splitlines()[-1][:60] if output.strip() else "(empty)"
                    lines.append(f"#{inst['idx']} {inst['project']}\n  {last}")
                await send_fn({"type": "text", "text": "\n\n".join(lines)})
            return

        elif cmd == "/focus":
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
            _focused_target = inst["target"]
            _focused_project = inst["project"]
            _focused_last_activity = time.time()
            output = capture_pane(_focused_target, 5)
            lines = output.strip().splitlines()[-3:] if output.strip() else []
            await send_fn({"type": "text", "text": f"Focused on [{_focused_project}]\n" + "\n".join(lines)})
            return

        elif cmd == "/unfocus":
            _focused_target = None
            _focused_project = None
            await send_fn({"type": "text", "text": "Unfocused."})
            return

        elif cmd == "/peek":
            target = _focused_target
            project = _focused_project
            if len(parts) > 1:
                instances = get_instances_info()
                try:
                    idx = int(parts[1]) - 1
                    target = instances[idx]["target"]
                    project = instances[idx]["project"]
                except (IndexError, ValueError):
                    pass
            if not target:
                await send_fn({"type": "text", "text": "No terminal. Use /focus first or /peek <n>"})
                return
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
        # Process as text (recursive)
        await handle_message({"type": "text", "text": text}, send_fn)
        return

    # Regular text in focused mode
    if _focused_target:
        # Check timeout
        if time.time() - _focused_last_activity > FOCUS_TIMEOUT:
            _focused_target = None
            _focused_project = None
            await send_fn({"type": "text", "text": "Auto-unfocused (idle > 5min)."})
            return

        _focused_last_activity = time.time()
        send_to_pane(_focused_target, f"[M] {text}")

        # Poll until done
        await asyncio.sleep(3)
        last_output = ""
        stable_count = 0
        for _ in range(60):
            output = capture_pane(_focused_target, 20)
            if output == last_output:
                stable_count += 1
                if stable_count >= 3:
                    break
            else:
                stable_count = 0
                last_output = output
            await asyncio.sleep(2)

        output = capture_pane(_focused_target, 15)
        cleaned = clean_terminal_output(output)
        lines = cleaned.splitlines()[-10:]
        await send_fn({"type": "text", "text": "\n".join(lines)[-2000:]})
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
<title>Claude Code Remote</title>
<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: -apple-system, system-ui, sans-serif; background: #1a1a2e; color: #eee; height: 100vh; display: flex; flex-direction: column; }
#header { background: #16213e; padding: 12px 16px; font-size: 16px; font-weight: 600; border-bottom: 1px solid #333; }
#status { font-size: 11px; color: #4CAF50; margin-top: 2px; }
#messages { flex: 1; overflow-y: auto; padding: 12px; }
.msg { margin-bottom: 10px; padding: 8px 12px; border-radius: 8px; max-width: 90%; word-wrap: break-word; white-space: pre-wrap; font-size: 14px; line-height: 1.4; }
.msg.user { background: #0f3460; margin-left: auto; }
.msg.bot { background: #2a2a4a; }
.msg code, .msg pre { font-family: 'SF Mono', Monaco, monospace; font-size: 12px; }
#input-area { background: #16213e; padding: 10px; border-top: 1px solid #333; display: flex; gap: 8px; align-items: center; }
#text-input { flex: 1; padding: 10px; border-radius: 20px; border: 1px solid #444; background: #1a1a2e; color: #eee; font-size: 15px; outline: none; }
#text-input:focus { border-color: #4CAF50; }
button { border: none; border-radius: 50%; width: 56px; height: 56px; cursor: pointer; display: flex; align-items: center; justify-content: center; -webkit-tap-highlight-color: transparent; }
#send-btn { background: #4CAF50; color: white; font-size: 22px; min-width: 56px; }
#mic-btn { background: #e53935; color: white; font-size: 26px; min-width: 56px; }
#mic-btn.recording { background: #f44336; animation: pulse 1s infinite; }
@keyframes pulse { 0%,100% { opacity: 1; } 50% { opacity: 0.5; } }
</style>
</head>
<body>
<div id="header">
  Claude Code Remote
  <div id="status">connecting...</div>
</div>
<div id="messages"></div>
<div id="input-area">
  <button id="mic-btn" ontouchstart="event.preventDefault();startMic()" ontouchend="event.preventDefault();stopMic()" onmousedown="startMic()" onmouseup="stopMic()">🎤</button>
  <input id="text-input" placeholder="Type or use voice..." onkeydown="if(event.key==='Enter')sendText()">
  <button id="send-btn" ontouchstart="event.preventDefault();sendText()" onclick="sendText()">→</button>
</div>

<script>
let ws;
let mediaRecorder;
let audioChunks = [];
let isRecording = false;

function connect() {
  const proto = (location.protocol === 'https:') ? 'wss:' : 'ws:';
  ws = new WebSocket(proto + '//' + location.host + '/ws');
  ws.onopen = () => { document.getElementById('status').textContent = 'connected'; };
  ws.onclose = () => {
    document.getElementById('status').textContent = 'disconnected - reconnecting...';
    setTimeout(connect, 2000);
  };
  ws.onmessage = (e) => {
    const msg = JSON.parse(e.data);
    addMessage(msg.text, 'bot');
  };
}

function addMessage(text, who) {
  const div = document.createElement('div');
  div.className = 'msg ' + who;
  div.textContent = text;
  document.getElementById('messages').appendChild(div);
  div.scrollIntoView({ behavior: 'smooth' });
}

function sendText() {
  const input = document.getElementById('text-input');
  const text = input.value.trim();
  if (!text) return;
  if (!ws || ws.readyState !== WebSocket.OPEN) {
    addMessage('[Not connected - reconnecting...]', 'bot');
    connect();
    return;
  }
  addMessage(text, 'user');
  ws.send(JSON.stringify({ type: 'text', text: text }));
  input.value = '';
  input.focus();
}

let micStream = null;

async function startMic() {
  if (isRecording) return;
  const btn = document.getElementById('mic-btn');
  try {
    micStream = await navigator.mediaDevices.getUserMedia({ audio: { sampleRate: 16000, channelCount: 1 } });
    mediaRecorder = new MediaRecorder(micStream, { mimeType: 'audio/webm' });
    audioChunks = [];
    mediaRecorder.ondataavailable = (e) => audioChunks.push(e.data);
    mediaRecorder.onstop = async () => {
      micStream.getTracks().forEach(t => t.stop());
      micStream = null;
      const blob = new Blob(audioChunks, { type: 'audio/webm' });
      const reader = new FileReader();
      reader.onload = () => {
        const base64 = reader.result.split(',')[1];
        addMessage('[voice]', 'user');
        ws.send(JSON.stringify({ type: 'audio', data: base64 }));
      };
      reader.readAsDataURL(blob);
    };
    mediaRecorder.start();
    isRecording = true;
    btn.classList.add('recording');
    btn.textContent = '⏹';
  } catch (err) {
    addMessage('Mic error: ' + err.message, 'bot');
  }
}

function stopMic() {
  if (!isRecording) return;
  const btn = document.getElementById('mic-btn');
  isRecording = false;
  btn.classList.remove('recording');
  btn.textContent = '🎤';
  if (mediaRecorder && mediaRecorder.state !== 'inactive') {
    mediaRecorder.stop();
  }
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

    @app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket):
        await websocket.accept()
        log.info("Client connected: %s", websocket.client)
        connected = True

        async def send_fn(msg):
            if connected:
                try:
                    await websocket.send_json(msg)
                except Exception:
                    pass

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
            connected = False
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

    if not os.path.exists(cert_file):
        os.makedirs(cert_dir, exist_ok=True)
        log.info("Generating self-signed certificate for HTTPS...")
        import subprocess as _sp2
        _sp2.run([
            "openssl", "req", "-x509", "-newkey", "rsa:2048",
            "-keyout", key_file, "-out", cert_file,
            "-days", "365", "-nodes",
            "-subj", f"/CN={local_ip}",
            "-addext", f"subjectAltName=IP:{local_ip}",
        ], capture_output=True)

    print(f"\n  NOTE: First time on phone, accept the security warning")
    print(f"  (self-signed certificate is safe on your private network)")
    print("", flush=True)

    uvicorn.run(app, host=args.host, port=args.port, log_level="warning",
                ssl_certfile=cert_file, ssl_keyfile=key_file)


if __name__ == "__main__":
    main()
