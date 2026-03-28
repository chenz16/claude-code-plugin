"""
Unified entry point for claude-code-plugin.

Usage:
    claude-plugin voice [args]        — Voice input
    claude-plugin screenshot [args]   — Screenshot input
    claude-plugin remote [args]       — Remote access (Telegram or --web)
    claude-plugin status              — Show running services
    claude-plugin help                — Show this help
"""

import sys


HELP = """Claude Code Plugin — voice, screenshot, and remote access for Claude Code

Commands:
  claude-plugin voice [args]        Start voice input
  claude-plugin screenshot [args]   Start screenshot clipboard monitor
  claude-plugin remote              Start Telegram bot
  claude-plugin remote --web        Start local web server (phone access)
  claude-plugin status              Show what's running
  claude-plugin help                Show this help

Examples:
  claude-plugin voice                       # local paste mode
  claude-plugin voice --host user@ip        # send to remote server
  claude-plugin remote --web                # phone control via web
  claude-plugin screenshot                  # auto-detect target
"""


def main():
    if len(sys.argv) < 2 or sys.argv[1] in ("help", "--help", "-h"):
        print(HELP)
        return

    cmd = sys.argv[1]
    # Remove the subcommand from argv so the sub-module sees correct args
    sys.argv = [sys.argv[0]] + sys.argv[2:]

    if cmd == "voice":
        from voice.cli import main as voice_main
        voice_main()
    elif cmd in ("screenshot", "ss"):
        from screenshot.cli import main as ss_main
        ss_main()
    elif cmd == "remote":
        from remote.cli import main as remote_main
        remote_main()
    elif cmd == "status":
        import subprocess
        print("Claude Code Plugin Status:\n")
        for name, pattern in [("voice", "voice_input"), ("screenshot", "screenshot_input"),
                               ("remote (telegram)", "tmux_bot"), ("remote (web)", "web_server")]:
            ret = subprocess.run(["pgrep", "-fa", pattern], capture_output=True, text=True)
            if ret.stdout.strip():
                print(f"  {name}: running")
            else:
                print(f"  {name}: stopped")
    else:
        print(f"Unknown command: {cmd}")
        print("Run 'claude-plugin help' for usage.")
        sys.exit(1)


if __name__ == "__main__":
    main()
