#!/usr/bin/env python3
"""
Screenshot input for Claude Code — clipboard monitoring approach.

Monitors the system clipboard for new screenshots. When detected:
  - Saves the image to a local temp directory
  - Local mode: puts the file path on clipboard for easy paste into Claude Code
  - WSL mode: copies to WSL filesystem and sends path to tmux
  - Remote mode: SCP to remote server and sends path to tmux

Works with ANY screenshot tool — Win+Shift+S, Cmd+Shift+4, Flameshot, etc.

Usage:
  claude-screenshot                              # local mode (Windows/macOS)
  claude-screenshot --wsl                        # send to Claude Code in WSL
  claude-screenshot --host user@remote-ip        # send to remote server
  claude-screenshot --hosts user@ip1,user@ip2    # multi-host auto-detect
  claude-screenshot --auto                       # auto-detect SSH connections
"""

import os
import platform
import subprocess
import hashlib
import time
import argparse
import threading

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from shared.config import SCREENSHOT_LOCAL_DIR, SCREENSHOT_REMOTE_DIR
from shared.ssh_remote import (
    test_ssh, get_active_session, send_to_remote_tmux,
    ensure_remote_dir, scp_to_remote, list_remote_sessions,
)

IS_MAC = platform.system() == "Darwin"
IS_WIN = platform.system() == "Windows"
IS_LINUX = platform.system() == "Linux"

args = None
screenshot_counter = 0
last_image_hash = None


# ── Clipboard image detection ──

def get_clipboard_image():
    """Get image from clipboard. Returns PIL Image or None."""
    try:
        from PIL import ImageGrab
        img = ImageGrab.grabclipboard()
        if img is not None and hasattr(img, 'tobytes'):
            return img
    except Exception:
        pass

    # Linux fallback: xclip
    if IS_LINUX:
        try:
            ret = subprocess.run(
                ["xclip", "-selection", "clipboard", "-t", "image/png", "-o"],
                capture_output=True, timeout=3,
            )
            if ret.returncode == 0 and ret.stdout:
                from PIL import Image
                import io
                return Image.open(io.BytesIO(ret.stdout))
        except Exception:
            pass

    return None


def image_hash(img):
    """Get a hash of a PIL Image to detect changes."""
    return hashlib.md5(img.tobytes()).hexdigest()


# ── Save and transfer ──

def save_screenshot(img):
    """Save PIL Image to local temp dir. Returns file path."""
    global screenshot_counter
    screenshot_counter += 1

    os.makedirs(SCREENSHOT_LOCAL_DIR, exist_ok=True)
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    filename = f"screenshot_{timestamp}_{screenshot_counter}.png"
    local_path = os.path.join(SCREENSHOT_LOCAL_DIR, filename)

    img.save(local_path, "PNG")
    size_kb = os.path.getsize(local_path) / 1024
    print(f"  Saved: {filename} ({size_kb:.0f} KB)", flush=True)
    return local_path


def handle_local(local_path):
    """Local mode: auto-type file path into focused window."""
    try:
        if IS_MAC:
            import pyperclip
            from pynput.keyboard import Key, Controller
            kb = Controller()
            pyperclip.copy(local_path)
            time.sleep(0.1)
            kb.press(Key.cmd)
            kb.press("v")
            kb.release("v")
            kb.release(Key.cmd)
        else:
            # Linux: use xclip + xdotool (Ctrl+Shift+V for terminal paste)
            subprocess.run(["xclip", "-selection", "clipboard"],
                           input=local_path.encode(), timeout=5)
            time.sleep(0.1)
            subprocess.run(["xdotool", "key", "--clearmodifiers", "ctrl+shift+v"],
                           timeout=5)
        print(f"  Path auto-pasted to focused window!", flush=True)
    except Exception as e:
        print(f"  Path: {local_path}", flush=True)
        print(f"  (auto-paste failed: {e})", flush=True)


def handle_wsl(local_path):
    """WSL mode: copy to WSL filesystem and send path to Claude Code tmux."""
    # Convert Windows path to WSL-accessible path
    wsl_screenshot_dir = "/tmp/claude-screenshots"
    filename = os.path.basename(local_path)
    wsl_path = f"{wsl_screenshot_dir}/{filename}"

    # Ensure dir exists in WSL
    subprocess.run(
        ["wsl", "mkdir", "-p", wsl_screenshot_dir],
        capture_output=True, timeout=10,
    )

    # Copy file to WSL
    # Windows path like C:\Users\...\file.png -> wsl can access via /mnt/c/Users/...
    # But easier: just use wsl cp from the Windows path
    win_path_for_wsl = local_path.replace("\\", "/")
    # Convert drive letter: C:/... -> /mnt/c/...
    if len(win_path_for_wsl) >= 2 and win_path_for_wsl[1] == ":":
        drive = win_path_for_wsl[0].lower()
        win_path_for_wsl = f"/mnt/{drive}{win_path_for_wsl[2:]}"

    subprocess.run(
        ["wsl", "cp", win_path_for_wsl, wsl_path],
        capture_output=True, timeout=10,
    )

    # Find active tmux session in WSL
    ret = subprocess.run(
        ["wsl", "tmux", "list-clients", "-F", "#{client_activity} #{session_name}"],
        capture_output=True, text=True, timeout=10,
    )
    session = None
    if ret.returncode == 0 and ret.stdout.strip():
        # Most recent client
        lines = ret.stdout.strip().splitlines()
        lines.sort(reverse=True)
        session = lines[0].split()[-1] if lines else None

    if not session:
        ret = subprocess.run(
            ["wsl", "tmux", "list-sessions", "-F", "#{session_activity} #{session_name}"],
            capture_output=True, text=True, timeout=10,
        )
        if ret.returncode == 0 and ret.stdout.strip():
            lines = ret.stdout.strip().splitlines()
            lines.sort(reverse=True)
            session = lines[0].split()[-1] if lines else None

    if not session:
        print(f"  No tmux session found in WSL.", flush=True)
        print(f"  Screenshot saved at (WSL): {wsl_path}", flush=True)
        return

    # Send path to tmux
    escaped = wsl_path.replace("'", "'\\''")
    subprocess.run(
        ["wsl", "tmux", "send-keys", "-t", session, f"{escaped}"],
        capture_output=True, timeout=10,
    )

    print(f"  -> WSL tmux:{session}", flush=True)
    print(f"  Image path sent! Press Enter in Claude Code to include it.", flush=True)


def handle_remote(local_path, host):
    """Remote mode: SCP to remote and send path to tmux."""
    filename = os.path.basename(local_path)
    remote_dir = args.remote_dir
    remote_path = f"{remote_dir}/{filename}"

    ensure_remote_dir(host, remote_dir)

    print(f"  Transferring to {host}...", flush=True)
    if not scp_to_remote(local_path, host, remote_path):
        print("  ERROR: SCP failed.", flush=True)
        return

    session = get_active_session(host)
    if not session:
        print("  ERROR: No active tmux session on remote.", flush=True)
        print(f"  Screenshot saved at: {host}:{remote_path}", flush=True)
        return

    send_to_remote_tmux(remote_path, host, session)
    print(f"  -> {host} tmux:{session}", flush=True)
    print(f"  Image path sent! Press Enter in Claude Code to include it.", flush=True)


# ── Active terminal detection (same approach as voice_input.py) ──

# Terminal PID and known remote hosts
_terminal_pid = None
_remote_hosts = {}  # ip -> user@host


def find_terminal_pid():
    """Find the PID of the terminal emulator by walking up from current process."""
    try:
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
            if any(t in comm.lower() for t in ["terminator", "terminal", "x-terminal",
                                                 "gnome-term", "konsole", "xterm",
                                                 "alacritty", "kitty"]):
                return int(pid)
            pid = ppid
        # Fallback: active window PID
        ret = subprocess.run(
            ["xprop", "-root", "_NET_ACTIVE_WINDOW"],
            capture_output=True, text=True, timeout=2,
        )
        wid = ret.stdout.strip().split()[-1]
        ret = subprocess.run(
            ["xprop", "-id", wid, "_NET_WM_PID"],
            capture_output=True, text=True, timeout=2,
        )
        return int(ret.stdout.strip().split()[-1])
    except Exception:
        return None


def scan_ssh_connections():
    """Scan running SSH processes to find remote hosts. Returns {ip: user@host}."""
    discovered = {}
    try:
        ret = subprocess.run(
            ["pgrep", "-a", "ssh"],
            capture_output=True, text=True, timeout=3,
        )
        for line in ret.stdout.strip().splitlines():
            parts = line.split()
            for part in parts[1:]:
                if "@" in part and not part.startswith("-") and "/" not in part and not part.startswith("["):
                    ip = part.split("@")[-1]
                    if ip and not ip.startswith("-"):
                        discovered[ip] = part
    except Exception:
        pass
    return discovered


def detect_active_target():
    """Detect whether the user's active terminal tab is SSH'd to a remote host.

    Checks pts mtime to find the most recently active tab, then checks if
    that tab has an SSH child process to a known remote host.

    Returns: ("remote", ssh_host_str) or ("local", None)
    Pure local operations, <10ms.
    """
    if not _terminal_pid:
        return "local", None

    try:
        ret = subprocess.run(
            ["ps", "--ppid", str(_terminal_pid), "-o", "pid,tty", "--no-headers"],
            capture_output=True, text=True, timeout=3,
        )

        pts_pids = {}
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

        # Check if this pts has SSH to any known remote host
        bash_pid = pts_pids[best_pts]
        ret = subprocess.run(
            ["pgrep", "-a", "-P", bash_pid],
            capture_output=True, text=True, timeout=3,
        )
        for line in ret.stdout.strip().splitlines():
            if "ssh" not in line:
                continue
            for ip, host_str in _remote_hosts.items():
                if ip in line:
                    return "remote", host_str
            # Auto-discover new host
            parts = line.split()
            for part in parts[1:]:
                if "@" in part and not part.startswith("-") and "/" not in part:
                    ip = part.split("@")[-1]
                    if ip and not ip.startswith("-"):
                        _remote_hosts[ip] = part
                        print(f"  [auto] Discovered new remote host: {part}", flush=True)
                        return "remote", part

        return "local", None
    except Exception:
        return "local", None


# ── Legacy detection for Windows ──

def check_wsl_tmux():
    """Check if WSL has an active tmux session. Returns True if found."""
    if not IS_WIN:
        return False
    try:
        ret = subprocess.run(
            ["wsl", "tmux", "list-sessions"],
            capture_output=True, text=True, timeout=5,
        )
        return ret.returncode == 0 and ret.stdout.strip() != ""
    except Exception:
        return False


# ── Main loop ──

def on_new_screenshot(img):
    """Handle a newly detected screenshot.

    On Linux: uses pts-based detection (same as voice_input.py)
      - Active tab has SSH to remote -> SCP + send to remote tmux
      - Otherwise -> paste path into local window

    On Windows/macOS: legacy detection (WSL tmux -> SSH scan -> local)
    """
    print(f"\n  New screenshot detected!", flush=True)
    local_path = save_screenshot(img)

    if args.host:
        # Explicit host mode
        handle_remote(local_path, args.host)
    elif args.wsl:
        handle_wsl(local_path)
    elif IS_LINUX and _terminal_pid:
        # Linux: pts-based auto-detection
        mode, host = detect_active_target()
        if mode == "remote" and host:
            print(f"  [auto] Active tab -> {host}", flush=True)
            handle_remote(local_path, host)
        else:
            print(f"  [auto] Active tab -> LOCAL", flush=True)
            handle_local(local_path)
    else:
        # Windows/macOS fallback
        if IS_WIN and check_wsl_tmux():
            handle_wsl(local_path)
        else:
            ssh_hosts = scan_ssh_connections()
            if len(ssh_hosts) == 1:
                host = list(ssh_hosts.values())[0]
                print(f"  Auto-detected: SSH to {host}", flush=True)
                handle_remote(local_path, host)
            else:
                handle_local(local_path)

    if args.cleanup and os.path.exists(local_path):
        os.remove(local_path)


def clipboard_monitor_loop():
    """Main loop: poll clipboard for new images."""
    global last_image_hash

    while True:
        try:
            img = get_clipboard_image()
            if img is not None:
                h = image_hash(img)
                if h != last_image_hash:
                    last_image_hash = h
                    threading.Thread(
                        target=on_new_screenshot,
                        args=(img,),
                        daemon=True,
                    ).start()
        except Exception as e:
            pass  # Silently continue on clipboard errors
        time.sleep(0.5)


def main():
    global args, _terminal_pid, _remote_hosts

    parser = argparse.ArgumentParser(
        description="Screenshot input for Claude Code. "
        "Monitors clipboard for new screenshots and auto-sends to Claude Code. "
        "No flags needed — auto-detects active terminal tab (local vs SSH remote)."
    )
    parser.add_argument("--wsl", action="store_true",
                        help="Force send to WSL tmux (Windows only)")
    parser.add_argument("--host",
                        help="Force send to this SSH host (e.g. user@remote-ip)")
    parser.add_argument("--remote-dir", default=SCREENSHOT_REMOTE_DIR,
                        help=f"Remote screenshot directory (default: {SCREENSHOT_REMOTE_DIR})")
    parser.add_argument("--no-cleanup", dest="cleanup", action="store_false", default=True,
                        help="Keep local screenshot copies after transfer")
    args = parser.parse_args()

    # Validate mode
    if args.wsl and not IS_WIN:
        print("ERROR: --wsl is only available on Windows.")
        exit(1)

    # Check PIL
    try:
        from PIL import ImageGrab
    except ImportError:
        print("ERROR: Pillow not installed.")
        print("  pip install Pillow")
        exit(1)

    # Test SSH if explicit host
    if args.host:
        print(f"Testing SSH to {args.host}...", flush=True)
        if not test_ssh(args.host):
            print(f"ERROR: Cannot SSH to {args.host}. Set up SSH key auth first.")
            exit(1)
        print("SSH OK.", flush=True)
        ensure_remote_dir(args.host, args.remote_dir)
        ip = args.host.split("@")[-1]
        _remote_hosts[ip] = args.host

    # Linux: set up pts-based auto-detection
    if IS_LINUX and not args.host and not args.wsl:
        _terminal_pid = find_terminal_pid()
        if _terminal_pid:
            print(f"  [auto] Terminal PID: {_terminal_pid}", flush=True)

        # Auto-discover active SSH connections
        _remote_hosts = scan_ssh_connections()
        if _remote_hosts:
            print(f"  [auto] SSH hosts: {', '.join(_remote_hosts.values())}", flush=True)
            # Test and prepare remote dirs
            for ip, host_str in list(_remote_hosts.items()):
                if test_ssh(host_str):
                    ensure_remote_dir(host_str, args.remote_dir)
                else:
                    del _remote_hosts[ip]

    # WSL check
    if args.wsl:
        ret = subprocess.run(["wsl", "echo", "ok"], capture_output=True, text=True, timeout=10)
        if ret.returncode != 0:
            print("ERROR: Cannot access WSL.")
            exit(1)
        print("WSL OK.", flush=True)

    # Mode description
    if args.wsl:
        mode_str = "WSL tmux"
    elif args.host:
        mode_str = f"Remote: {args.host}"
    elif IS_LINUX and _terminal_pid:
        mode_str = "AUTO (active tab: SSH remote -> remote tmux, local -> paste)"
    else:
        mode_str = "Auto-detect"

    plat = "Windows" if IS_WIN else ("macOS" if IS_MAC else "Linux")
    print("")
    print("=== Screenshot Input for Claude Code ===")
    print(f"  Platform: {plat}")
    print(f"  Mode: {mode_str}")
    print("")
    print("  Take a screenshot (Shift+Ctrl+PrintScreen or any tool)")
    print("  Screenshots are detected from clipboard automatically.")
    print("  Press Ctrl+C to stop.")
    print("", flush=True)

    try:
        clipboard_monitor_loop()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
