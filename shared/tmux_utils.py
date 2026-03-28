"""
Shared local tmux operations.

Provides utilities for discovering Claude Code instances in tmux,
capturing pane output, and sending keystrokes to panes.
Used primarily by the remote access (Telegram bot) module.
"""

import shlex
import subprocess
from pathlib import Path


def sh(cmd, timeout=15):
    """Run a shell command and return combined stdout+stderr."""
    try:
        r = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, timeout=timeout
        )
        return (r.stdout + r.stderr).strip()
    except subprocess.TimeoutExpired:
        return "[command timed out]"
    except Exception as e:
        return f"[error: {e}]"


def find_claude_instances():
    """Find all interactive Claude Code processes and map them to tmux panes.

    Returns a list of dicts: {"pid", "project", "cwd", "target"}
    """
    # Build pane_pid -> tmux-target mapping
    pane_map = {}
    raw = sh(
        "tmux list-panes -a -F '#{pane_pid} #{session_name}:#{window_index}.#{pane_index}'"
    )
    if not raw or raw.startswith("["):
        return []
    for line in raw.splitlines():
        parts = line.strip().split(" ", 1)
        if len(parts) == 2:
            pane_map[parts[0]] = parts[1]

    # Find claude processes (exclude pipe-mode calls)
    instances = []
    ps_out = sh("ps -eo pid,ppid,args --no-headers")
    for line in ps_out.splitlines():
        parts = line.split(None, 2)
        if len(parts) < 3:
            continue
        pid, ppid, args = parts[0], parts[1], parts[2]

        if "claude" not in args.lower():
            continue
        if "-p" in args or "--pipe" in args:
            continue
        if "tmux_bot" in args:
            continue
        if args.startswith("node ") or args.startswith("npm "):
            continue

        cwd = sh(f"readlink /proc/{pid}/cwd 2>/dev/null")
        if not cwd or cwd.startswith("["):
            continue

        project = Path(cwd).name

        # Map process to tmux pane via parent PID chain
        target = pane_map.get(ppid)
        if not target:
            gppid = sh(f"ps -o ppid= -p {ppid} 2>/dev/null").strip()
            target = pane_map.get(gppid)
        if not target:
            gppid = sh(f"ps -o ppid= -p {ppid} 2>/dev/null").strip()
            if gppid:
                ggppid = sh(f"ps -o ppid= -p {gppid} 2>/dev/null").strip()
                target = pane_map.get(ggppid)

        if target:
            if not any(i["target"] == target for i in instances):
                instances.append({
                    "pid": pid,
                    "project": project,
                    "cwd": cwd,
                    "target": target,
                })

    return instances


def capture_pane(target, lines=40):
    """Capture recent visible output from a tmux pane."""
    return sh(f"tmux capture-pane -t {shlex.quote(target)} -p -S -{lines}")


def send_to_pane(target, text, press_enter=True):
    """Send keystrokes to a tmux pane."""
    escaped = text.replace("\\", "\\\\").replace("'", "'\\''")
    enter_part = " Enter" if press_enter else ""
    sh(f"tmux send-keys -t {shlex.quote(target)} '{escaped}'{enter_part}")
