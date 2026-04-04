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

    print("=" * 50)
    print("  Claude Code Plugin")
    print("  Starting voice + screenshot + web server...")
    print("=" * 50)
    print("", flush=True)

    # Start web server as a separate process (uvicorn needs its own event loop)
    import subprocess as _sp
    import atexit
    import os
    import re
    import time as _time
    web_proc = _sp.Popen(
        [sys.executable, "-m", "remote.web_server", "--no-ssl"],
        stdout=None, stderr=None,
    )
    print(f"  [web] Started (PID {web_proc.pid})", flush=True)
    atexit.register(lambda: web_proc.terminate())

    # Start cloudflared tunnel for public URL
    cf_bin = "/tmp/cloudflared"
    if not os.path.exists(cf_bin):
        print("  [tunnel] Downloading cloudflared...", flush=True)
        dl_ret = _sp.run(
            ["curl", "-sL",
             "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64",
             "-o", cf_bin],
            timeout=60,
        )
        os.chmod(cf_bin, 0o755)

    cf_proc = _sp.Popen(
        [cf_bin, "tunnel", "--url", "http://localhost:8080"],
        stdout=_sp.DEVNULL, stderr=_sp.PIPE,
    )
    atexit.register(lambda: cf_proc.terminate())

    # Wait for tunnel URL from cloudflared stderr
    tunnel_url = None
    deadline = _time.time() + 15
    while _time.time() < deadline:
        line = b""
        # Read one byte at a time with a short timeout to avoid blocking
        while _time.time() < deadline:
            cf_proc.stderr.flush() if hasattr(cf_proc.stderr, 'flush') else None
            import select
            ready, _, _ = select.select([cf_proc.stderr], [], [], 0.5)
            if not ready:
                continue
            ch = cf_proc.stderr.read(1)
            if not ch:
                break
            line += ch
            if ch == b"\n":
                break
        decoded = line.decode("utf-8", errors="replace")
        m = re.search(r"(https://[a-zA-Z0-9-]+\.trycloudflare\.com)", decoded)
        if m:
            tunnel_url = m.group(1)
            break

    if tunnel_url:
        print(f"\n  [tunnel] Public URL: {tunnel_url}", flush=True)
        try:
            import qrcode
            qr = qrcode.QRCode(border=1)
            qr.add_data(tunnel_url)
            qr.make()
            qr.print_ascii(invert=True)
        except ImportError:
            pass
        print("", flush=True)
    else:
        print("  [tunnel] Warning: could not get tunnel URL (timeout)", flush=True)

    # Read remaining cloudflared stderr in background so pipe doesn't block
    def _drain_cf_stderr():
        try:
            for _ in cf_proc.stderr:
                pass
        except Exception:
            pass
    _cf_drain = threading.Thread(target=_drain_cf_stderr, daemon=True)
    _cf_drain.start()

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
            if ss_mod._terminal_pid:
                print(f"  [screenshot] Terminal PID: {ss_mod._terminal_pid}", flush=True)

            ss_mod._remote_hosts = scan_ssh_connections()
            if ss_mod._remote_hosts:
                print(f"  [screenshot] SSH hosts: {', '.join(ss_mod._remote_hosts.values())}", flush=True)
                for ip, host_str in list(ss_mod._remote_hosts.items()):
                    if test_ssh(host_str):
                        ensure_remote_dir(host_str, SSArgs.remote_dir)
                    else:
                        del ss_mod._remote_hosts[ip]

            print("  [screenshot] Monitoring clipboard...", flush=True)
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
