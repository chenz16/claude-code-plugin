#!/usr/bin/env python3
"""
Voice input for Claude Code on remote servers (Linux + evdev).
Hold RIGHT ALT to record, release to stop and auto-transcribe.
Sends text to remote tmux session via SSH.

Usage:
  python -m voice.voice_input --host user@remote-ip

Dependencies:
  pip install funasr modelscope evdev
  sudo apt install alsa-utils
  User must be in 'input' group: sudo usermod -aG input $USER
"""

import os
import subprocess
import signal
import threading
import argparse
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

        session = get_active_session(args.host)
        if not session:
            print("  ERROR: No active tmux session found on remote.", flush=True)
            return

        print(f"  -> tmux:{session}", flush=True)
        send_to_remote_tmux(text, args.host, session)
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
    global args

    parser = argparse.ArgumentParser(
        description="Voice input for Claude Code on remote servers. "
        "Uses Alibaba SenseVoice for Chinese speech recognition."
    )
    parser.add_argument("--host", required=True, help="SSH host (e.g. user@remote-ip)")
    args = parser.parse_args()

    for cmd in ["arecord", "ssh"]:
        if subprocess.run(["which", cmd], capture_output=True).returncode != 0:
            print(f"ERROR: '{cmd}' not found.")
            exit(1)

    print(f"Testing SSH to {args.host}...", flush=True)
    if not test_ssh(args.host):
        print(f"ERROR: Cannot SSH to {args.host}. Set up SSH key auth first.")
        exit(1)
    print("SSH OK.", flush=True)

    sessions = list_remote_sessions(args.host)
    if sessions:
        print(f"  Available sessions: {', '.join(sessions)}", flush=True)

    dev = require_keyboard()

    # Pre-load model
    from shared.transcribe import load_model
    load_model()

    print("")
    print("=== Voice Input for Claude Code ===")
    print(f"  Remote: {args.host}")
    print("  Auto-detects active tmux session each time.")
    print("  Hold RIGHT ALT to record, release to stop. (global, any window)")
    print("  Text appears in tmux - press Enter yourself to confirm.")
    print("", flush=True)

    keyboard_loop(dev)


if __name__ == "__main__":
    main()
