"""
CLI entry point for claude-screenshot.
Linux only - captures screenshots and sends to remote Claude Code via SSH + tmux.
"""

import sys
import platform


def main():
    os_name = platform.system()

    if os_name != "Linux":
        print("claude-screenshot is Linux only.")
        print("It requires maim/scrot, xdotool, and evdev for global hotkeys.")
        if os_name == "Windows":
            print("On Windows, use Win+Shift+S to screenshot, then paste into Claude Code.")
        sys.exit(1)

    from screenshot.screenshot_input import main as screenshot_main
    screenshot_main()


if __name__ == "__main__":
    main()
