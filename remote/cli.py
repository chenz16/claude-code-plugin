"""
CLI entry point for claude-remote.
Cross-platform Telegram bot for remote Claude Code session management.
"""

import sys
import os


def main():
    # Check if .env exists next to this file and auto-load
    cli_dir = os.path.dirname(os.path.abspath(__file__))
    env_file = os.path.join(cli_dir, ".env")
    if os.path.exists(env_file):
        with open(env_file) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, _, value = line.partition("=")
                    key = key.strip()
                    value = value.strip().strip('"').strip("'")
                    if key and value:
                        os.environ.setdefault(key, value)

    from remote.tmux_bot import main as bot_main
    bot_main()


if __name__ == "__main__":
    main()
