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
  claude-plugin start [args]        Start voice + screenshot together
  claude-plugin voice [args]        Start voice input only
  claude-plugin screenshot [args]   Start screenshot monitor only
  claude-plugin remote              Start Telegram bot
  claude-plugin remote --web        Start local web server (phone access)
  claude-plugin status              Show what's running
  claude-plugin stop                Stop all running services
  claude-plugin help                Show this help

Examples:
  claude-plugin start                       # auto-detect local/remote
  claude-plugin start --host user@ip        # with explicit remote host
  claude-plugin voice --auto                # voice only, auto-detect
  claude-plugin screenshot                  # screenshot only, auto-detect
"""


def main():
    if len(sys.argv) < 2 or sys.argv[1] in ("help", "--help", "-h"):
        print(HELP)
        return

    cmd = sys.argv[1]
    # Remove the subcommand from argv so the sub-module sees correct args
    sys.argv = [sys.argv[0]] + sys.argv[2:]

    if cmd == "start":
        _start_all()
    elif cmd == "voice":
        from voice.cli import main as voice_main
        voice_main()
    elif cmd in ("screenshot", "ss"):
        from screenshot.cli import main as ss_main
        ss_main()
    elif cmd == "remote":
        from remote.cli import main as remote_main
        remote_main()
    elif cmd == "status":
        _show_status()
    elif cmd == "stop":
        _stop_all()
    else:
        print(f"Unknown command: {cmd}")
        print("Run 'claude-plugin help' for usage.")
        sys.exit(1)


def _start_all():
    """Start voice + screenshot together in one process."""
    import threading

    # Voice needs --auto (or --host from user args), screenshot needs no args
    # sys.argv already has args after 'start' stripped

    # Add --auto to voice args if no --host specified
    voice_args = sys.argv[:]
    if "--host" not in voice_args and "--auto" not in voice_args:
        voice_args.append("--auto")

    import subprocess as _sp
    import atexit
    import os
    import re
    import time as _time

    print("Starting claude-plugin...", flush=True)

    # Start web server quietly
    web_proc = _sp.Popen(
        [sys.executable, "-m", "remote.web_server", "--no-ssl"],
        stdout=_sp.DEVNULL, stderr=_sp.DEVNULL,
    )
    atexit.register(lambda: web_proc.terminate())
    print("  [web] OK", flush=True)

    # Start cloudflared tunnel for public URL
    cf_bin = "/tmp/cloudflared"
    if not os.path.exists(cf_bin):
        print("  [tunnel] Downloading...", flush=True)
        _sp.run(["curl", "-sL",
                 "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64",
                 "-o", cf_bin], timeout=60)
        os.chmod(cf_bin, 0o755)

    cf_proc = _sp.Popen(
        [cf_bin, "tunnel", "--url", "http://localhost:8080"],
        stdout=_sp.DEVNULL, stderr=_sp.PIPE, text=True,
    )
    atexit.register(lambda: cf_proc.terminate())

    tunnel_url_found = threading.Event()
    def _read_cf():
        try:
            for line in cf_proc.stderr:
                m = re.search(r"(https://[a-zA-Z0-9-]+\.trycloudflare\.com)", line)
                if m and not tunnel_url_found.is_set():
                    tunnel_url_found.url = m.group(1)
                    tunnel_url_found.set()
        except Exception:
            pass
    threading.Thread(target=_read_cf, daemon=True).start()
    tunnel_url_found.wait(timeout=15)
    if tunnel_url_found.is_set():
        url = tunnel_url_found.url
        print(f"  [tunnel] {url}", flush=True)
        try:
            import qrcode
            qr = qrcode.QRCode(border=1)
            qr.add_data(url)
            qr.make()
            qr.print_ascii(invert=True)
        except ImportError:
            pass
        print("  Scan to connect from phone.\n", flush=True)
    else:
        print("  [tunnel] timeout", flush=True)

    # Start screenshot monitor in background thread
    def run_screenshot():
        try:
            from screenshot.screenshot_input import clipboard_monitor_loop, \
                find_terminal_pid, scan_ssh_connections, ensure_remote_dir, test_ssh
            from screenshot.screenshot_input import _remote_hosts as _ss_hosts
            import screenshot.screenshot_input as ss_mod

            # Initialize screenshot module directly (skip argparse)
            class SSArgs:
                host = None
                wsl = False
                remote_dir = "/tmp/claude-screenshots"
                cleanup = True
            ss_mod.args = SSArgs()

            # Set up auto-detection
            ss_mod._terminal_pid = find_terminal_pid()
            ss_mod._remote_hosts = scan_ssh_connections()
            if ss_mod._remote_hosts:
                for ip, host_str in list(ss_mod._remote_hosts.items()):
                    if test_ssh(host_str):
                        ensure_remote_dir(host_str, SSArgs.remote_dir)
                    else:
                        del ss_mod._remote_hosts[ip]

            clipboard_monitor_loop()
        except KeyboardInterrupt:
            pass
        except Exception as e:
            print(f"\n  [screenshot] Error: {e}", flush=True)

    ss_thread = threading.Thread(target=run_screenshot, daemon=True)
    ss_thread.start()

    # Run voice in main thread (evdev needs main thread)
    try:
        sys.argv = voice_args
        from voice.cli import main as voice_main
        voice_main()
    except KeyboardInterrupt:
        print("\nStopping...")


def _show_status():
    """Show running services."""
    import subprocess
    print("Claude Code Plugin Status:\n")
    for name, pattern in [("voice", "voice_input"), ("screenshot", "screenshot_input"),
                           ("remote (telegram)", "tmux_bot"), ("remote (web)", "web_server")]:
        ret = subprocess.run(["pgrep", "-fa", pattern], capture_output=True, text=True)
        if ret.stdout.strip():
            print(f"  {name}: running")
        else:
            print(f"  {name}: stopped")


def _stop_all():
    """Stop all running plugin services."""
    import subprocess
    for pattern in ["voice_input", "screenshot_input", "tmux_bot", "web_server"]:
        ret = subprocess.run(["pgrep", "-f", pattern], capture_output=True, text=True)
        pids = ret.stdout.strip().splitlines()
        if pids:
            for pid in pids:
                subprocess.run(["kill", pid.strip()], capture_output=True)
            print(f"  Stopped {pattern} ({len(pids)} process(es))")
        else:
            print(f"  {pattern}: not running")


if __name__ == "__main__":
    main()
