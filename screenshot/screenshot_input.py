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
    """Local mode: auto-type file path into focused window (like voice does)."""
    try:
        import pyperclip
        from pynput.keyboard import Key, Controller
        kb = Controller()

        pyperclip.copy(local_path)
        time.sleep(0.1)

        if IS_MAC:
            kb.press(Key.cmd)
            kb.press("v")
            kb.release("v")
            kb.release(Key.cmd)
        else:
            kb.press(Key.ctrl)
            kb.press("v")
            kb.release("v")
            kb.release(Key.ctrl)

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


# ── SSH host detection (for multi-host / auto mode) ──

def detect_all_ssh_hosts():
    """Scan all active SSH connections."""
    hosts = set()
    try:
        if IS_WIN:
            # Windows: check for ssh.exe processes
            ret = subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq ssh.exe", "/FO", "CSV", "/NH"],
                capture_output=True, text=True, timeout=5,
            )
            # Also check via wsl
            ret2 = subprocess.run(
                ["wsl", "pgrep", "-a", "ssh"],
                capture_output=True, text=True, timeout=5,
            )
            for line in ret2.stdout.strip().splitlines():
                parts = line.split()
                # Find user@host pattern in ssh command args
                for p in parts:
                    if "@" in p and not p.startswith("-"):
                        hosts.add(p)
        else:
            ret = subprocess.run(
                ["pgrep", "-a", "ssh"],
                capture_output=True, text=True, timeout=5,
            )
            for line in ret.stdout.strip().splitlines():
                parts = line.split()
                for p in parts:
                    if "@" in p and not p.startswith("-"):
                        hosts.add(p)
    except Exception:
        pass
    return list(hosts)


def resolve_target_host():
    """Determine which host to send the screenshot to."""
    if args.host:
        return args.host

    # Auto/multi-host: scan SSH connections
    all_hosts = detect_all_ssh_hosts()

    if args.hosts:
        allowed = set(args.hosts)
        for h in all_hosts:
            if h in allowed:
                return h
            bare = h.split("@")[-1] if "@" in h else h
            for a in allowed:
                if bare == (a.split("@")[-1] if "@" in a else a):
                    return a
        print(f"  No matching SSH host found. Active: {all_hosts}, Allowed: {args.hosts}", flush=True)
        return None

    if len(all_hosts) == 1:
        host = all_hosts[0]
        print(f"  Auto-detected SSH host: {host}", flush=True)
        return host
    elif all_hosts:
        print(f"  Multiple SSH connections: {all_hosts}", flush=True)
        print(f"  Use --host or --hosts to specify target.", flush=True)
    else:
        print("  No active SSH connections found.", flush=True)
    return None


# ── Main loop ──

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


def on_new_screenshot(img):
    """Handle a newly detected screenshot.

    Auto-detection priority:
      1. If --host/--hosts/--auto specified → remote mode
      2. If --wsl specified → WSL mode
      3. Auto: check WSL tmux → check SSH connections → local paste
    """
    print(f"\n  New screenshot detected!", flush=True)
    local_path = save_screenshot(img)

    sent = False

    if args.host or args.hosts or args.auto:
        # Explicit remote mode
        host = resolve_target_host()
        if host:
            handle_remote(local_path, host)
            sent = True
    elif args.wsl:
        # Explicit WSL mode
        handle_wsl(local_path)
        sent = True
    else:
        # Auto-detect: WSL tmux → SSH → local
        if IS_WIN and check_wsl_tmux():
            print(f"  Auto-detected: Claude Code in WSL tmux", flush=True)
            handle_wsl(local_path)
            sent = True
        else:
            # Check for SSH connections
            ssh_hosts = detect_all_ssh_hosts()
            if len(ssh_hosts) == 1:
                print(f"  Auto-detected: SSH to {ssh_hosts[0]}", flush=True)
                handle_remote(local_path, ssh_hosts[0])
                sent = True
            elif not ssh_hosts:
                # Local mode: auto-type into focused window
                handle_local(local_path)
                sent = True
            else:
                print(f"  Multiple SSH connections: {ssh_hosts}", flush=True)
                print(f"  Use --host to specify, falling back to local paste.", flush=True)
                handle_local(local_path)
                sent = True

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
    global args

    parser = argparse.ArgumentParser(
        description="Screenshot input for Claude Code. "
        "Monitors clipboard for new screenshots and auto-sends to Claude Code. "
        "No flags needed — auto-detects WSL tmux, SSH connections, or pastes locally."
    )
    target = parser.add_mutually_exclusive_group()
    target.add_argument("--wsl", action="store_true",
                        help="Force send to WSL tmux (Windows only)")
    target.add_argument("--host",
                        help="Force send to this SSH host (e.g. user@remote-ip)")
    target.add_argument("--hosts",
                        help="Comma-separated SSH hosts for multi-host mode")
    target.add_argument("--auto", action="store_true",
                        help="Force auto-detect from SSH connections")
    parser.add_argument("--remote-dir", default=SCREENSHOT_REMOTE_DIR,
                        help=f"Remote screenshot directory (default: {SCREENSHOT_REMOTE_DIR})")
    parser.add_argument("--no-cleanup", dest="cleanup", action="store_false", default=True,
                        help="Keep local screenshot copies after transfer")
    args = parser.parse_args()

    if args.hosts:
        args.hosts = [h.strip() for h in args.hosts.split(",") if h.strip()]

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

    # Test SSH if remote mode
    if args.host:
        print(f"Testing SSH to {args.host}...", flush=True)
        if not test_ssh(args.host):
            print(f"ERROR: Cannot SSH to {args.host}. Set up SSH key auth first.")
            exit(1)
        print("SSH OK.", flush=True)
        ensure_remote_dir(args.host, args.remote_dir)
        sessions = list_remote_sessions(args.host)
        if sessions:
            print(f"  Available sessions: {', '.join(sessions)}", flush=True)

    if args.hosts:
        print(f"Testing SSH to {len(args.hosts)} hosts...", flush=True)
        for host in args.hosts:
            ok = test_ssh(host)
            print(f"  {host}: {'OK' if ok else 'FAILED'}", flush=True)

    # Test WSL access
    if args.wsl:
        ret = subprocess.run(["wsl", "echo", "ok"], capture_output=True, text=True, timeout=10)
        if ret.returncode != 0:
            print("ERROR: Cannot access WSL.")
            exit(1)
        print("WSL OK.", flush=True)
        # Check tmux in WSL
        ret = subprocess.run(
            ["wsl", "tmux", "list-sessions", "-F", "#{session_name}"],
            capture_output=True, text=True, timeout=10,
        )
        if ret.returncode == 0 and ret.stdout.strip():
            print(f"  WSL tmux sessions: {', '.join(ret.stdout.strip().splitlines())}", flush=True)
        else:
            print("  Warning: no tmux sessions found in WSL.", flush=True)

    # Determine mode description
    if args.wsl:
        mode_str = "WSL tmux (forced)"
    elif args.host:
        mode_str = f"Remote: {args.host} (forced)"
    elif args.hosts:
        mode_str = f"Remote multi-host: {', '.join(args.hosts)}"
    elif args.auto:
        mode_str = "Remote auto-detect (forced)"
    else:
        mode_str = "Auto-detect (WSL tmux -> SSH -> local paste)"

    plat = "Windows" if IS_WIN else ("macOS" if IS_MAC else "Linux")
    print("")
    print("=== Screenshot Input for Claude Code ===")
    print(f"  Platform: {plat}")
    print(f"  Mode: {mode_str}")
    print("")
    print("  Take a screenshot with your favorite tool:")
    if IS_WIN:
        print("    Win+Shift+S  (Snipping Tool)")
    elif IS_MAC:
        print("    Cmd+Shift+4  (region)  /  Cmd+Shift+3  (full screen)")
    else:
        print("    Flameshot, Shutter, PrintScreen, or any tool")
    print("")
    print("  Screenshots are detected from clipboard automatically.")
    print("  Press Ctrl+C to stop.")
    print("", flush=True)

    try:
        clipboard_monitor_loop()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
