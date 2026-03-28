"""
Chinese voice input -> Claude Code (Windows)
Hold Right Alt to record, release to stop, auto-transcribe and paste.
Uses Alibaba SenseVoiceSmall model.
"""

import sys
import os
import threading
import queue
import time
import numpy as np
import sounddevice as sd
import pyperclip
from pynput import keyboard
from pynput.keyboard import Key, Controller

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from shared.config import SAMPLE_RATE, CHANNELS, BLOCK_SIZE, MIN_DURATION
from shared.transcribe import load_model, transcribe_audio

audio_queue = queue.Queue()
is_recording = False
kb_controller = Controller()


def audio_callback(indata, frames, time_info, status):
    if is_recording:
        audio_queue.put(indata.copy())


def paste_text(text):
    """Paste text into focused window, restore clipboard after."""
    if not text:
        return
    old_clipboard = ""
    try:
        old_clipboard = pyperclip.paste()
    except Exception:
        pass

    pyperclip.copy(text)
    time.sleep(0.05)
    kb_controller.press(Key.ctrl)
    kb_controller.press("v")
    kb_controller.release("v")
    kb_controller.release(Key.ctrl)
    time.sleep(0.1)

    try:
        pyperclip.copy(old_clipboard)
    except Exception:
        pass


def start_recording():
    global is_recording
    if is_recording:
        return
    is_recording = True
    while not audio_queue.empty():
        audio_queue.get()
    print("[recording] Recording... release Right Alt to stop")


def stop_recording():
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

            # Check if user mentions a screenshot → grab from clipboard first
            from shared.clipboard_image import has_screenshot_intent, grab_clipboard_image
            if has_screenshot_intent(text):
                img_path = grab_clipboard_image()
                if img_path:
                    paste_text(img_path)
                    time.sleep(0.2)
                    # Send Enter to confirm the image path, then paste the text
                    from pynput.keyboard import Key
                    kb_controller.press(Key.enter)
                    kb_controller.release(Key.enter)
                    time.sleep(0.2)

            paste_text(text)
        else:
            print("[warning] No speech detected")

    threading.Thread(target=do_transcribe, daemon=True).start()


def main():
    print("=" * 50)
    print("  Chinese Voice Input (SenseVoiceSmall)")
    print("  Hold Right Alt to speak, release to transcribe and paste")
    print("  Press Esc to exit")
    print("=" * 50)

    load_model()

    def on_press(key):
        if key == Key.alt_r or key == Key.alt_gr:
            start_recording()

    def on_release(key):
        if key == Key.alt_r or key == Key.alt_gr:
            stop_recording()
        elif key == Key.esc:
            print("\nExited.")
            os._exit(0)

    listener = keyboard.Listener(on_press=on_press, on_release=on_release)
    listener.start()

    with sd.InputStream(
        samplerate=SAMPLE_RATE,
        channels=CHANNELS,
        callback=audio_callback,
        blocksize=BLOCK_SIZE,
    ):
        print("\nAudio stream started, waiting for voice input...\n")
        try:
            while listener.is_alive():
                listener.join(timeout=0.5)
        except KeyboardInterrupt:
            print("\nExited.")
            listener.stop()


if __name__ == "__main__":
    main()
