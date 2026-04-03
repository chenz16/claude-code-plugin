"""
CLI entry point for claude-voice.
Auto-detects platform and runs the appropriate voice input module.

Platform routing:
  Windows  → voice_input_win.py   (sounddevice + pynput + Ctrl+V paste)
  macOS    → voice_input_linux.py (sounddevice + pynput + pbcopy/Cmd+V paste)
  WSL      → voice_input_linux.py (sounddevice + pynput)
  Linux    → voice_input.py       (arecord + evdev, SSH remote mode)
           → voice_input_linux.py (local mode)
"""

import sys
import platform


def main():
    os_name = platform.system()

    if os_name == "Windows":
        from voice.voice_input_win import main as win_main
        win_main()

    elif os_name == "Darwin":
        # macOS: uses sounddevice + pynput (same engine as Linux local mode)
        from voice.voice_input_linux import main as linux_main
        linux_main()

    elif os_name == "Linux":
        # Check if WSL
        is_wsl = False
        try:
            with open("/proc/version", "r") as f:
                is_wsl = "microsoft" in f.read().lower()
        except Exception:
            pass

        if is_wsl:
            from voice.voice_input_linux import main as linux_main
            linux_main()
        else:
            # Native Linux
            if "--host" in sys.argv or "--auto" in sys.argv:
                # Remote/auto mode: use evdev + arecord (more reliable)
                from voice.voice_input import main as remote_main
                remote_main()
            else:
                from voice.voice_input_linux import main as linux_main
                linux_main()
    else:
        print(f"Unsupported platform: {os_name}")
        print("Supported: Windows, macOS, Linux, WSL")
        sys.exit(1)


if __name__ == "__main__":
    main()
