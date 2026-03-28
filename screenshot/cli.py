"""
CLI entry point for claude-screenshot.
Clipboard-based screenshot detection — works on Windows, macOS, and Linux.
"""


def main():
    from screenshot.screenshot_input import main as screenshot_main
    screenshot_main()


if __name__ == "__main__":
    main()
