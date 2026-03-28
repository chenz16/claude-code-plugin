#!/usr/bin/env python3
"""
Screenshot input for Claude Code on remote servers.
Captures locally, transfers to remote, sends image path to tmux.

Hotkeys:
  Linux:  PrintScreen = full screen, Right Ctrl = region
  macOS:  Ctrl+Shift+3 = full screen, Ctrl+Shift+4 = region

Modes:
  1. Single-host:  --host user@remote-ip
  2. Multi-host:   --hosts user@ip1,user@ip2  (auto-detect from window)
  3. Full auto:    --auto  (scan all SSH connections)

Supports: Linux (evdev + maim/scrot) and macOS (pynput + screencapture)
"""

import os
import platform
import subprocess
import threading
import time
import argparse

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from shared.config import SCREENSHOT_LOCAL_DIR, SCREENSHOT_REMOTE_DIR
from shared.ssh_remote import (
    test_ssh, get_active_session, send_to_remote_tmux,
    ensure_remote_dir, scp_to_remote, list_remote_sessions,
)

IS_MAC = platform.system() == "Darwin"

args = None
screenshot_counter = 0
lock = threading.Lock()


# ── Auto-detect SSH host from focused window ──

def get_focused_window_pid():
    """Get the PID of the currently focused window."""
    if IS_MAC:
        # macOS: use AppleScript to get frontmost app PID
        try:
            ret = subprocess.run(
                ["osascript", "-e",
                 'tell application "System Events" to get unix id of first process whose frontmost is true'],
                capture_output=True, text=True, timeout=3,
            )
            if ret.returncode == 0 and ret.stdout.strip():
                return int(ret.stdout.strip())
        except Exception:
            pass
        return None

    # Linux: xdotool
    try:
        wid = subprocess.run(
            ["xdotool", "getactivewindow"],
            capture_output=True, text=True, timeout=3,
        )
        if wid.returncode != 0:
            return None
        window_id = wid.stdout.strip()

        pid_ret = subprocess.run(
            ["xdotool", "getwindowpid", window_id],
            capture_output=True, text=True, timeout=3,
        )
        if pid_ret.returncode != 0:
            return None
        return int(pid_ret.stdout.strip())
    except Exception:
        return None


def get_child_pids(pid):
    """Recursively get all child PIDs of a process."""
    try:
        ret = subprocess.run(
            ["pgrep", "-P", str(pid)],
            capture_output=True, text=True, timeout=3,
        )
        children = [int(p) for p in ret.stdout.strip().splitlines() if p]
        all_children = list(children)
        for child in children:
            all_children.extend(get_child_pids(child))
        return all_children
    except Exception:
        return []


def get_ssh_host_from_pid(pid):
    """Extract the SSH destination host from a process's cmdline."""
    try:
        if IS_MAC:
            # macOS: use ps to get command line
            ret = subprocess.run(
                ["ps", "-o", "args=", "-p", str(pid)],
                capture_output=True, text=True, timeout=3,
            )
            if ret.returncode != 0 or not ret.stdout.strip():
                return None
            cmdline = ret.stdout.strip().split()
        else:
            # Linux: read /proc
            cmdline_path = f"/proc/{pid}/cmdline"
            if not os.path.exists(cmdline_path):
                return None
            with open(cmdline_path, "rb") as f:
                cmdline = f.read().decode("utf-8", errors="replace").split("\x00")

        if not cmdline or "ssh" not in os.path.basename(cmdline[0]):
            return None

        skip_next = False
        for arg in cmdline[1:]:
            if skip_next:
                skip_next = False
                continue
            if arg.startswith("-"):
                if arg in ("-p", "-l", "-i", "-o", "-F", "-J", "-L", "-R", "-D",
                           "-W", "-w", "-E", "-S", "-b", "-c", "-m", "-O"):
                    skip_next = True
                continue
            if arg and not arg.startswith("-"):
                return arg
    except Exception:
        pass
    return None


def detect_active_host():
    """Detect which SSH host the focused terminal is connected to."""
    window_pid = get_focused_window_pid()
    if not window_pid:
        print("  Could not detect focused window PID.", flush=True)
        return None

    all_pids = [window_pid] + get_child_pids(window_pid)

    found_hosts = []
    for pid in all_pids:
        host = get_ssh_host_from_pid(pid)
        if host:
            found_hosts.append(host)

    if not found_hosts:
        print("  No SSH connection found in active window.", flush=True)
        return None

    if args.hosts:
        allowed = set(args.hosts)
        for h in found_hosts:
            if h in allowed:
                return h
            bare = h.split("@")[-1] if "@" in h else h
            for a in allowed:
                a_bare = a.split("@")[-1] if "@" in a else a
                if bare == a_bare:
                    return a
        print(f"  SSH host(s) {found_hosts} not in allowed list {args.hosts}", flush=True)
        return None

    return found_hosts[-1]


def detect_all_ssh_hosts():
    """Scan all active SSH connections on the system."""
    hosts = set()
    try:
        ret = subprocess.run(
            ["pgrep", "-a", "ssh"],
            capture_output=True, text=True, timeout=5,
        )
        for line in ret.stdout.strip().splitlines():
            parts = line.split(None, 1)
            if len(parts) < 2:
                continue
            pid = int(parts[0])
            host = get_ssh_host_from_pid(pid)
            if host:
                hosts.add(host)
    except Exception:
        pass
    return list(hosts)


# ── Screenshot capture ──

def take_screenshot(region=False):
    """Take a screenshot locally. Returns the local file path or None."""
    global screenshot_counter
    with lock:
        screenshot_counter += 1
        count = screenshot_counter

    timestamp = time.strftime("%Y%m%d_%H%M%S")
    filename = f"screenshot_{timestamp}_{count}.png"
    local_path = os.path.join(SCREENSHOT_LOCAL_DIR, filename)

    print(f"\n  Capturing {'region' if region else 'full screen'}...", flush=True)

    try:
        if IS_MAC:
            # macOS: screencapture (built-in)
            if region:
                ret = subprocess.run(["screencapture", "-i", local_path], capture_output=True, text=True, timeout=30)
            else:
                ret = subprocess.run(["screencapture", local_path], capture_output=True, text=True, timeout=10)
        else:
            # Linux: maim or scrot
            if region:
                ret = subprocess.run(["maim", "-s", local_path], capture_output=True, text=True, timeout=30)
            else:
                ret = subprocess.run(["maim", local_path], capture_output=True, text=True, timeout=10)

            if ret.returncode != 0:
                print("  maim failed, trying scrot...", flush=True)
                if region:
                    ret = subprocess.run(["scrot", "-s", local_path], capture_output=True, text=True, timeout=30)
                else:
                    ret = subprocess.run(["scrot", local_path], capture_output=True, text=True, timeout=10)

        if ret.returncode != 0:
            print(f"  ERROR: Screenshot capture failed: {ret.stderr}", flush=True)
            return None

        if not os.path.exists(local_path):
            print("  ERROR: Screenshot file not created.", flush=True)
            return None

        size_kb = os.path.getsize(local_path) / 1024
        print(f"  Captured: {filename} ({size_kb:.0f} KB)", flush=True)
        return local_path

    except subprocess.TimeoutExpired:
        print("  ERROR: Screenshot capture timed out.", flush=True)
        return None
    except Exception as e:
        print(f"  ERROR: {e}", flush=True)
        return None


# ── Main pipeline ──

def resolve_target_host():
    """Determine which host to send the screenshot to."""
    if args.host:
        return args.host

    host = detect_active_host()
    if host:
        print(f"  Detected active SSH host: {host}", flush=True)
    else:
        print("  WARNING: Could not detect SSH host from active window.", flush=True)
        if args.auto:
            all_hosts = detect_all_ssh_hosts()
            if len(all_hosts) == 1:
                host = all_hosts[0]
                print(f"  Fallback: using only active SSH connection: {host}", flush=True)
            elif all_hosts:
                print(f"  Multiple SSH connections found: {all_hosts}", flush=True)
                print("  Cannot determine target. Focus the correct terminal and retry.", flush=True)
            else:
                print("  No active SSH connections found.", flush=True)
    return host


def transfer_and_send(local_path, host):
    """Transfer screenshot to remote server and send path to tmux."""
    filename = os.path.basename(local_path)
    remote_dir = args.remote_dir
    remote_path = f"{remote_dir}/{filename}"

    ensure_remote_dir(host, remote_dir)

    print(f"  Transferring to {host}...", flush=True)
    if not scp_to_remote(local_path, host, remote_path):
        print("  ERROR: SCP failed.", flush=True)
        return

    print(f"  Remote path: {remote_path}", flush=True)

    session = get_active_session(host)
    if not session:
        print("  ERROR: No active tmux session found on remote.", flush=True)
        print(f"  Screenshot saved at: {host}:{remote_path}", flush=True)
        return

    send_to_remote_tmux(remote_path, host, session)

    print(f"  -> {host} tmux:{session}", flush=True)
    print("  Image path sent! Press Enter in Claude Code to include it.", flush=True)

    if args.cleanup:
        os.remove(local_path)

    if IS_MAC:
        print("\n  Ready for next screenshot... (Ctrl+Shift+3=full, Ctrl+Shift+4=region)", flush=True)
    else:
        print("\n  Ready for next screenshot... (PrintScreen=full, RIGHT CTRL=region)", flush=True)


def handle_screenshot(region=False):
    """Full pipeline: detect host -> capture -> transfer -> send to tmux."""
    host = resolve_target_host()
    if not host:
        print("  Aborted: no target host.", flush=True)
        return

    local_path = take_screenshot(region=region)
    if local_path:
        transfer_and_send(local_path, host)


# ── Keyboard handling ──

def keyboard_loop_evdev():
    """Linux: keyboard loop via evdev."""
    import evdev
    from evdev import ecodes
    from shared.hotkey import require_keyboard

    dev = require_keyboard()

    for event in dev.read_loop():
        if event.type != ecodes.EV_KEY:
            continue
        key_event = evdev.categorize(event)

        if key_event.scancode == ecodes.KEY_SYSRQ:
            if key_event.keystate == key_event.key_down:
                threading.Thread(target=handle_screenshot, kwargs={"region": False}, daemon=True).start()

        elif key_event.scancode == ecodes.KEY_RIGHTCTRL:
            if key_event.keystate == key_event.key_down:
                threading.Thread(target=handle_screenshot, kwargs={"region": True}, daemon=True).start()


def keyboard_loop_pynput():
    """macOS: keyboard loop via pynput with Ctrl+Shift+3/4."""
    from pynput import keyboard
    from pynput.keyboard import Key

    pressed_keys = set()

    def on_press(key):
        pressed_keys.add(key)
        # Ctrl+Shift+3 = full screen
        if (Key.ctrl_l in pressed_keys or Key.ctrl_r in pressed_keys) and \
           (Key.shift_l in pressed_keys or Key.shift_r in pressed_keys):
            try:
                if hasattr(key, 'char') and key.char == '3':
                    threading.Thread(target=handle_screenshot, kwargs={"region": False}, daemon=True).start()
                elif hasattr(key, 'char') and key.char == '4':
                    threading.Thread(target=handle_screenshot, kwargs={"region": True}, daemon=True).start()
            except AttributeError:
                pass

    def on_release(key):
        pressed_keys.discard(key)

    listener = keyboard.Listener(on_press=on_press, on_release=on_release)
    listener.start()
    try:
        while listener.is_alive():
            listener.join(timeout=0.5)
    except KeyboardInterrupt:
        print("\nExited.")
        listener.stop()


def main():
    global args

    parser = argparse.ArgumentParser(
        description="Screenshot input for Claude Code on remote servers."
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--host", help="Single SSH host (e.g. user@remote-ip)")
    group.add_argument("--hosts", help="Comma-separated SSH hosts for auto-detect")
    group.add_argument("--auto", action="store_true", help="Auto-detect from active SSH connections")
    parser.add_argument("--remote-dir", default=SCREENSHOT_REMOTE_DIR,
                        help=f"Remote screenshot directory (default: {SCREENSHOT_REMOTE_DIR})")
    parser.add_argument("--no-cleanup", dest="cleanup", action="store_false", default=True,
                        help="Keep local screenshot copies")
    args = parser.parse_args()

    if args.hosts:
        args.hosts = [h.strip() for h in args.hosts.split(",") if h.strip()]

    # Check tools
    if IS_MAC:
        # macOS: screencapture is built-in, just check ssh/scp
        for cmd in ["scp", "ssh"]:
            if subprocess.run(["which", cmd], capture_output=True).returncode != 0:
                print(f"ERROR: '{cmd}' not found.")
                exit(1)
    else:
        # Linux: check maim/scrot
        has_maim = subprocess.run(["which", "maim"], capture_output=True).returncode == 0
        has_scrot = subprocess.run(["which", "scrot"], capture_output=True).returncode == 0
        if not has_maim and not has_scrot:
            print("ERROR: Neither 'maim' nor 'scrot' found.")
            print("  sudo apt install maim  (recommended)")
            exit(1)

        for cmd in ["scp", "ssh", "xdotool"]:
            if subprocess.run(["which", cmd], capture_output=True).returncode != 0:
                if cmd == "xdotool" and args.host:
                    continue
                print(f"ERROR: '{cmd}' not found.")
                exit(1)

    # Test SSH
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

    os.makedirs(SCREENSHOT_LOCAL_DIR, exist_ok=True)

    if args.host:
        mode_str = f"Single host: {args.host}"
    elif args.hosts:
        mode_str = f"Multi-host: {', '.join(args.hosts)} (auto-detect from active window)"
    else:
        mode_str = "Auto-detect: scanning active SSH connections"

    print("")
    print("=== Screenshot Input for Claude Code ===")
    print(f"  Platform: {'macOS' if IS_MAC else 'Linux'}")
    print(f"  Mode: {mode_str}")
    print(f"  Remote dir: {args.remote_dir}")
    print("")
    if IS_MAC:
        print("  Hotkeys:")
        print("    Ctrl+Shift+3  -> capture full screen")
        print("    Ctrl+Shift+4  -> capture selected region")
    else:
        print("  Hotkeys:")
        print("    PrintScreen   -> capture full screen")
        print("    RIGHT CTRL    -> capture selected region")
    print("")
    print("  Screenshot path is sent to Claude Code input.")
    print("  Press Enter in Claude Code to include the image.")
    print("", flush=True)

    if IS_MAC:
        keyboard_loop_pynput()
    else:
        keyboard_loop_evdev()


if __name__ == "__main__":
    main()
