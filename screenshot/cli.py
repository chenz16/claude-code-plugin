"""
CLI entry point for claude-screenshot.
Supports Linux and macOS. Not available on Windows.
"""

import sys
import platform


def main():
    os_name = platform.system()

    if os_name == "Windows":
        print("claude-screenshot is not available on Windows.")
        print("Use Win+Shift+S to screenshot, then paste into Claude Code.")
        sys.exit(1)

    if os_name not in ("Linux", "Darwin"):
        print(f"Unsupported platform: {os_name}")
        sys.exit(1)

    from screenshot.screenshot_input import main as screenshot_main
    screenshot_main()


if __name__ == "__main__":
    main()
