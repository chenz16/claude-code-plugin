"""
CLI entry point for claude-voice.
Auto-detects platform and runs the appropriate voice input module.
"""

import sys
import platform


def main():
    os_name = platform.system()

    if os_name == "Windows":
        from voice.voice_input_win import main as win_main
        win_main()
    elif os_name == "Linux":
        # Check if WSL
        is_wsl = False
        try:
            with open("/proc/version", "r") as f:
                is_wsl = "microsoft" in f.read().lower()
        except Exception:
            pass

        if is_wsl:
            # WSL: check if --host is passed -> use SSH remote mode
            # otherwise use local paste mode (pynput + sounddevice)
            if "--host" in sys.argv:
                from voice.voice_input_linux import main as linux_main
                linux_main()
            else:
                # WSL local mode: prefer voice_input_win style (sounddevice + pynput)
                # but since we're in WSL, use voice_input_linux which handles both
                from voice.voice_input_linux import main as linux_main
                linux_main()
        else:
            # Native Linux
            if "--host" in sys.argv:
                # Remote mode: use evdev + arecord (more reliable)
                from voice.voice_input import main as remote_main
                remote_main()
            else:
                # Local mode
                from voice.voice_input_linux import main as linux_main
                linux_main()
    else:
        print(f"Unsupported platform: {os_name}")
        print("Supported: Windows, Linux, WSL")
        sys.exit(1)


if __name__ == "__main__":
    main()
