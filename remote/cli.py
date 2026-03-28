"""
CLI entry point for claude-remote.
Supports two modes:
  claude-remote           → Telegram bot
  claude-remote --web     → Local web server (faster, private)
"""

import sys
import os


def _load_env():
    """Auto-load .env file if it exists."""
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


def main():
    if "--web" in sys.argv:
        sys.argv.remove("--web")
        from remote.web_server import main as web_main
        web_main()
    else:
        _load_env()
        from remote.tmux_bot import main as bot_main
        bot_main()


if __name__ == "__main__":
    main()
