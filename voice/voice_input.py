#!/usr/bin/env python3
"""
Voice input for Claude Code (Linux + evdev).
Hold RIGHT ALT to record, release to stop and auto-transcribe.

Auto-detects whether the user is in a local or remote terminal:
  - Finds the most recently active terminal tab (by pts mtime)
  - If that tab has SSH to a configured remote host -> send to remote tmux
  - Otherwise -> paste into the current focused window

Remote hosts are configured in ~/.config/claude-voice/hosts.conf (one per line).
If the file doesn't exist, it's auto-created from --host arg.

Usage:
  claude-voice --host user@remote-ip     # remote only (also saves to hosts.conf)
  claude-voice --auto                    # auto local/remote (uses hosts.conf)
  claude-voice --host user@ip --auto     # auto + add host to config

Dependencies:
  pip install funasr modelscope evdev
  sudo apt install alsa-utils xdotool xclip
  User must be in 'input' group: sudo usermod -aG input $USER
"""

import os
import subprocess
import signal
import threading
import time
import argparse
from pathlib import Path
from evdev import ecodes

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from shared.config import TMPWAV
from shared.transcribe import transcribe_file
from shared.hotkey import require_keyboard
from shared.ssh_remote import test_ssh, list_remote_sessions, get_active_session, send_to_remote_tmux

recording = False
record_proc = None
lock = threading.Lock()
args = None

HOSTS_CONF = os.path.expanduser("~/.config/claude-voice/hosts.conf")

# Map: remote IP/hostname -> ssh host string (e.g. "100.78.72.86" -> "chen.zhang@100.78.72.86")
_remote_hosts = {}

# Terminal PID (Terminator parent of all bash tabs)
_terminal_pid = None


def load_hosts():
    """Load remote hosts from config file."""
    hosts = {}
    if os.path.exists(HOSTS_CONF):
        with open(HOSTS_CONF) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    # e.g. "chen.zhang@100.78.72.86"
                    ip = line.split("@")[-1]
                    hosts[ip] = line
    return hosts


def save_host(host_str):
    """Add a host to config if not already there."""
    os.makedirs(os.path.dirname(HOSTS_CONF), exist_ok=True)
    existing = set()
    if os.path.exists(HOSTS_CONF):
        with open(HOSTS_CONF) as f:
            existing = {l.strip() for l in f if l.strip() and not l.startswith("#")}
    if host_str not in existing:
        with open(HOSTS_CONF, "a") as f:
            f.write(host_str + "\n")
        print(f"  [config] Added {host_str} to {HOSTS_CONF}", flush=True)


def scan_ssh_connections():
    """Auto-discover active SSH connections and add to hosts config.
    Scans running SSH processes to find remote hosts."""
    try:
        ret = subprocess.run(
            ["pgrep", "-a", "ssh"],
            capture_output=True, text=True, timeout=3,
        )
        discovered = {}
        for line in ret.stdout.strip().splitlines():
            parts = line.split()
            # Look for "ssh user@host" pattern
            for part in parts[1:]:
                if "@" in part and not part.startswith("-"):
                    # Skip options, socket paths, etc
                    if "/" in part or part.startswith("["):
                        continue
                    ip = part.split("@")[-1]
                    # Basic IP/hostname validation
                    if ip and not ip.startswith("-"):
                        discovered[ip] = part
        return discovered
    except Exception:
        return {}


def find_terminal_pid():
    """Find the PID of the terminal emulator (Terminator, gnome-terminal, etc.)
    by looking at the parent of the current shell."""
    try:
        # Walk up from current process to find the terminal
        pid = os.getppid()
        for _ in range(5):
            ret = subprocess.run(
                ["ps", "-o", "ppid=,comm=", "-p", str(pid)],
                capture_output=True, text=True, timeout=3,
            )
            parts = ret.stdout.strip().split(None, 1)
            if len(parts) < 2:
                break
            ppid, comm = parts[0], parts[1]
            # Check if this is a terminal emulator
            if any(t in comm.lower() for t in ["terminator", "terminal", "x-terminal", "gnome-term", "konsole", "xterm", "alacritty", "kitty"]):
                return int(pid)
            pid = ppid
        # Fallback: check the window PID
        ret = subprocess.run(
            ["xprop", "-root", "_NET_ACTIVE_WINDOW"],
            capture_output=True, text=True, timeout=2,
        )
        wid = ret.stdout.strip().split()[-1]
        ret = subprocess.run(
            ["xprop", "-id", wid, "_NET_WM_PID"],
            capture_output=True, text=True, timeout=2,
        )
        win_pid = ret.stdout.strip().split()[-1]
        return int(win_pid)
    except Exception:
        return None


def detect_active_target():
    """Detect whether the user's active terminal tab is SSH'd to a remote host.

    1. Find all bash children of the terminal -> their pts
    2. Find which pts was most recently active (mtime)
    3. Check if that pts has an SSH child connecting to any configured remote host

    Returns: ("remote", ssh_host_str) or ("local", None)
    Pure local operations, runs in <10ms.
    """
    if not _terminal_pid or not _remote_hosts:
        return "local", None

    try:
        # Get all bash children and their pts
        ret = subprocess.run(
            ["ps", "--ppid", str(_terminal_pid), "-o", "pid,tty", "--no-headers"],
            capture_output=True, text=True, timeout=3,
        )

        pts_pids = {}  # pts_num -> bash_pid
        for line in ret.stdout.strip().splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[1].startswith("pts/"):
                pts_num = parts[1].split("/")[1]
                pts_pids[pts_num] = parts[0]

        if not pts_pids:
            return "local", None

        # Find most recently active pts
        best_time = 0
        best_pts = None
        for pts_num in pts_pids:
            try:
                mtime = os.stat(f"/dev/pts/{pts_num}").st_mtime
                if mtime > best_time:
                    best_time = mtime
                    best_pts = pts_num
            except OSError:
                continue

        if not best_pts:
            return "local", None

        # Check if this pts has SSH to any remote host
        bash_pid = pts_pids[best_pts]
        ret = subprocess.run(
            ["pgrep", "-a", "-P", bash_pid],
            capture_output=True, text=True, timeout=3,
        )
        for line in ret.stdout.strip().splitlines():
            if "ssh" not in line:
                continue
            # Check known hosts first
            for ip, host_str in _remote_hosts.items():
                if ip in line:
                    return "remote", host_str
            # Auto-discover: if SSH to unknown host, add it
            parts = line.split()
            for part in parts[1:]:
                if "@" in part and not part.startswith("-") and "/" not in part:
                    ip = part.split("@")[-1]
                    if ip and not ip.startswith("-"):
                        # New host discovered, save it
                        _remote_hosts[ip] = part
                        save_host(part)
                        print(f"  [auto] Discovered new remote host: {part}", flush=True)
                        return "remote", part

        return "local", None
    except Exception:
        return "local", None


def paste_local(text):
    """Paste text into the currently focused window via clipboard + Ctrl+Shift+V."""
    if not text:
        return
    subprocess.run(["xclip", "-selection", "clipboard"], input=text.encode(), timeout=5)
    time.sleep(0.05)
    subprocess.run(["xdotool", "key", "--clearmodifiers", "ctrl+shift+v"], timeout=5)


def start_recording():
    global record_proc, recording
    with lock:
        if recording:
            return
        recording = True
    print("\n  Recording... (release RIGHT ALT to stop)", flush=True)
    record_proc = subprocess.Popen(
        ["arecord", "-f", "S16_LE", "-r", "16000", "-c", "1", "-t", "wav", TMPWAV],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def stop_recording():
    global record_proc, recording
    with lock:
        if not recording:
            return
        recording = False
    if record_proc:
        record_proc.send_signal(signal.SIGINT)
        record_proc.wait()
        record_proc = None
    print("  Stopped. Transcribing...", flush=True)
    do_transcribe()


def do_transcribe():
    try:
        if not os.path.exists(TMPWAV):
            print("  No audio file found.", flush=True)
            return

        text = transcribe_file(TMPWAV)
        if not text:
            print("  No speech detected.", flush=True)
            return

        print(f"\n  >>> {text}", flush=True)

        # Determine target
        if args.auto:
            mode, host = detect_active_target()
        else:
            mode, host = "remote", args.host

        # Check if user mentions a screenshot -> grab from clipboard first
        from shared.clipboard_image import has_screenshot_intent, grab_screenshot
        if has_screenshot_intent(text):
            img_path = grab_screenshot()
            if img_path:
                if mode == "remote" and host:
                    session = get_active_session(host)
                    if session:
                        send_to_remote_tmux(img_path, host, session)
                        print(f"  -> screenshot sent to {host}:{session}", flush=True)
                else:
                    paste_local(img_path)
                    time.sleep(0.2)
                    print(f"  -> screenshot pasted locally", flush=True)

        if mode == "remote" and host:
            session = get_active_session(host)
            if not session:
                print(f"  ERROR: No active tmux session on {host}", flush=True)
                return
            print(f"  -> {host} tmux:{session}", flush=True)
            send_to_remote_tmux(text, host, session)
        else:
            print(f"  -> LOCAL paste", flush=True)
            paste_local(text)
        print("  Sent!", flush=True)

    except Exception as e:
        print(f"  Error: {e}", flush=True)
    finally:
        if os.path.exists(TMPWAV):
            os.remove(TMPWAV)
    print("\n  Hold RIGHT ALT to record again...", flush=True)


def keyboard_loop(dev):
    """Main loop reading keyboard events via evdev."""
    import evdev

    for event in dev.read_loop():
        if event.type != ecodes.EV_KEY:
            continue
        key_event = evdev.categorize(event)

        if key_event.scancode == ecodes.KEY_RIGHTALT:
            if key_event.keystate == key_event.key_down:
                if not recording:
                    start_recording()
            elif key_event.keystate == key_event.key_up:
                if recording:
                    threading.Thread(target=stop_recording, daemon=True).start()


def main():
    global args, _remote_hosts, _terminal_pid

    parser = argparse.ArgumentParser(
        description="Voice input for Claude Code. "
        "Uses Alibaba SenseVoice for Chinese speech recognition."
    )
    parser.add_argument("--host", help="SSH host (e.g. user@remote-ip). Saved to hosts.conf.")
    parser.add_argument("--auto", action="store_true",
                        help="Auto-detect local/remote by checking active terminal tab")
    args = parser.parse_args()

    for cmd in ["arecord", "ssh"]:
        if subprocess.run(["which", cmd], capture_output=True).returncode != 0:
            print(f"ERROR: '{cmd}' not found.")
            exit(1)

    # Load / save hosts config
    if args.host:
        save_host(args.host)

    _remote_hosts = load_hosts()

    # Auto-discover active SSH connections
    if args.auto:
        discovered = scan_ssh_connections()
        for ip, host_str in discovered.items():
            if ip not in _remote_hosts:
                _remote_hosts[ip] = host_str
                save_host(host_str)
                print(f"  [auto] Found active SSH: {host_str}", flush=True)

    if not _remote_hosts and not args.auto:
        print("ERROR: No remote hosts configured.")
        print(f"  Use --host user@ip or --auto to discover")
        exit(1)

    # Test SSH connections
    good_hosts = {}
    for ip, host_str in _remote_hosts.items():
        print(f"  Testing SSH to {host_str}...", end=" ", flush=True)
        if test_ssh(host_str):
            sessions = list_remote_sessions(host_str)
            print(f"OK. Sessions: {', '.join(sessions) if sessions else 'none'}", flush=True)
            good_hosts[ip] = host_str
        else:
            print(f"FAILED (will skip)", flush=True)
    _remote_hosts = good_hosts

    # Find terminal PID for auto-detection
    if args.auto:
        _terminal_pid = find_terminal_pid()
        if _terminal_pid:
            print(f"  [auto] Terminal PID: {_terminal_pid}", flush=True)
        else:
            print("  [auto] WARNING: Could not find terminal PID", flush=True)

    dev = require_keyboard()

    # Pre-load model
    from shared.transcribe import load_model
    load_model()

    print("")
    print("=== Voice Input for Claude Code ===")
    print(f"  Remote hosts: {', '.join(_remote_hosts.values())}")
    if args.auto:
        print("  Mode: AUTO")
        print("    Active tab has SSH to remote -> send to remote tmux")
        print("    Otherwise -> paste into current window")
    else:
        print(f"  Mode: Remote only -> {args.host}")
    print("  Hold RIGHT ALT to record, release to stop.")
    print("", flush=True)

    keyboard_loop(dev)


if __name__ == "__main__":
    main()
