"""
Chinese voice input -> Claude Code (Linux / WSL)
Hold Right Alt to record, release to stop, auto-transcribe and paste/send.
Uses Alibaba SenseVoiceSmall model.

Linux native / WSL supported:
- Linux native: evdev keyboard + arecord audio
- WSL: pynput keyboard + sounddevice audio

Usage:
    python -m voice.voice_input_linux              # local paste mode
    python -m voice.voice_input_linux --host user@ip  # remote tmux mode
"""

import sys
import os
import threading
import queue
import subprocess
import argparse
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from shared.config import SAMPLE_RATE, CHANNELS, BLOCK_SIZE, MIN_DURATION, IS_WSL
from shared.transcribe import load_model, transcribe_audio

audio_queue = queue.Queue()
is_recording = False


# ====== Output methods ======

def paste_text_local(text):
    """Local paste: use xdotool or xclip to paste into focused window."""
    if not text:
        return
    try:
        subprocess.run(
            ["xdotool", "type", "--clearmodifiers", text],
            timeout=5,
        )
    except FileNotFoundError:
        subprocess.run(["xclip", "-selection", "clipboard"], input=text.encode(), timeout=5)
        subprocess.run(["xdotool", "key", "ctrl+v"], timeout=5)


def send_text_remote(text, host):
    """Remote mode: send text to remote tmux session via SSH."""
    if not text:
        return
    from shared.ssh_remote import get_active_session, send_to_remote_tmux

    sessions_ret = subprocess.run(
        ["ssh", host, "tmux", "list-sessions", "-F", "#{session_name}"],
        capture_output=True, text=True, timeout=10,
    )
    sessions = sessions_ret.stdout.strip().split("\n")
    if not sessions or not sessions[0]:
        print("[error] No tmux session on remote")
        return
    session = sessions[0]

    send_to_remote_tmux(text, host, session)
    print(f"[sent] -> {host} tmux:{session}")


# ====== Recording methods ======

def record_with_sounddevice():
    """Record with sounddevice (WSL / universal)."""
    import sounddevice as sd

    def audio_callback(indata, frames, time_info, status):
        if is_recording:
            audio_queue.put(indata.copy())

    stream = sd.InputStream(
        samplerate=SAMPLE_RATE,
        channels=CHANNELS,
        callback=audio_callback,
        blocksize=BLOCK_SIZE,
    )
    stream.start()
    return stream


def record_with_arecord(tmp_path):
    """Record with arecord (Linux native, more reliable)."""
    proc = subprocess.Popen(
        ["arecord", "-f", "S16_LE", "-r", str(SAMPLE_RATE), "-c", "1", tmp_path],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return proc


# ====== Keyboard listeners ======

def use_evdev_listener(on_start, on_stop):
    """Listen via evdev (Linux native, no X11 needed)."""
    import evdev
    from evdev import ecodes

    devices = [evdev.InputDevice(path) for path in evdev.list_devices()]
    kbd = None
    for dev in devices:
        caps = dev.capabilities(verbose=False)
        if ecodes.EV_KEY in caps:
            kbd = dev
            break

    if kbd is None:
        print("[error] No keyboard device found, check input group membership")
        sys.exit(1)

    print(f"[info] Keyboard: {kbd.name}")

    for event in kbd.read_loop():
        if event.type == ecodes.EV_KEY and event.code == ecodes.KEY_RIGHTALT:
            if event.value == 1:
                on_start()
            elif event.value == 0:
                on_stop()


def use_pynput_listener(on_start, on_stop):
    """Listen via pynput (WSL / universal)."""
    from pynput import keyboard
    from pynput.keyboard import Key

    def on_press(key):
        if key == Key.alt_r or key == Key.alt_gr:
            on_start()

    def on_release(key):
        if key == Key.alt_r or key == Key.alt_gr:
            on_stop()
        elif key == Key.esc:
            print("\nExited.")
            os._exit(0)

    listener = keyboard.Listener(on_press=on_press, on_release=on_release)
    listener.start()
    return listener


# ====== Main ======

def main():
    global is_recording

    parser = argparse.ArgumentParser(description="Chinese voice input (Linux/WSL)")
    parser.add_argument("--host", help="Remote server user@ip (omit for local paste mode)")
    parser.add_argument("--use-evdev", action="store_true", help="Force evdev (needs input group)")
    args = parser.parse_args()

    mode = "remote" if args.host else "local"
    use_evdev = args.use_evdev and not IS_WSL

    print("=" * 50)
    print("  Chinese Voice Input (SenseVoiceSmall)")
    print(f"  Mode: {'remote -> ' + args.host if args.host else 'local paste'}")
    print(f"  Platform: {'WSL' if IS_WSL else 'Linux'}")
    print("  Hold Right Alt to speak, release to transcribe")
    print("  Esc or Ctrl+C to exit")
    print("=" * 50)

    load_model()
    stream = record_with_sounddevice()

    def on_start():
        global is_recording
        if is_recording:
            return
        is_recording = True
        while not audio_queue.empty():
            audio_queue.get()
        print("[recording] Recording... release Right Alt to stop")

    def on_stop():
        global is_recording
        if not is_recording:
            return
        is_recording = False
        print("[processing] Transcribing...")

        chunks = []
        while not audio_queue.empty():
            chunks.append(audio_queue.get())

        if not chunks:
            print("[warning] No audio captured")
            return

        audio_data = np.concatenate(chunks, axis=0)
        duration = len(audio_data) / SAMPLE_RATE
        if duration < MIN_DURATION:
            print(f"[skip] Too short ({duration:.1f}s)")
            return

        print(f"[info] Duration: {duration:.1f}s")

        def do_transcribe():
            text = transcribe_audio(audio_data)
            if text:
                print(f"[result] {text}")
                if mode == "remote":
                    send_text_remote(text, args.host)
                else:
                    paste_text_local(text)
            else:
                print("[warning] No speech detected")

        threading.Thread(target=do_transcribe, daemon=True).start()

    print("\nAudio stream started, waiting for voice input...\n")

    if use_evdev:
        try:
            use_evdev_listener(on_start, on_stop)
        except KeyboardInterrupt:
            print("\nExited.")
    else:
        listener = use_pynput_listener(on_start, on_stop)
        try:
            while listener.is_alive():
                listener.join(timeout=0.5)
        except KeyboardInterrupt:
            print("\nExited.")
            listener.stop()

    stream.stop()


if __name__ == "__main__":
    main()
