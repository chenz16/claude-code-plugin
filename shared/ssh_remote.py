"""
Shared SSH + remote tmux operations.

Provides utilities for:
- Testing SSH connections
- Detecting active tmux sessions on remote servers
- Sending text/paths to remote tmux sessions
"""

import subprocess


def test_ssh(host, timeout=5):
    """Test SSH connection. Returns True if successful."""
    ret = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "-o", f"ConnectTimeout={timeout}", host, "echo ok"],
        capture_output=True, text=True,
    )
    return ret.returncode == 0


def list_remote_sessions(host, timeout=10):
    """List tmux session names on remote host. Returns list of strings."""
    ret = subprocess.run(
        ["ssh", host, "tmux list-sessions -F '#{session_name}' 2>/dev/null"],
        capture_output=True, text=True, timeout=timeout,
    )
    sessions = ret.stdout.strip()
    return sessions.splitlines() if sessions else []


def get_active_session(host, timeout=10):
    """Auto-detect the most recently active tmux session on remote host."""
    # Try client activity first (most accurate)
    ret = subprocess.run(
        ["ssh", host,
         "tmux list-clients -F '#{client_activity} #{session_name}' 2>/dev/null "
         "| sort -rn | head -1 | awk '{print $2}'"],
        capture_output=True, text=True, timeout=timeout,
    )
    session = ret.stdout.strip()
    if session:
        return session

    # Fall back to session activity
    ret = subprocess.run(
        ["ssh", host,
         "tmux list-sessions -F '#{session_activity} #{session_name}' 2>/dev/null "
         "| sort -rn | head -1 | awk '{print $2}'"],
        capture_output=True, text=True, timeout=timeout,
    )
    return ret.stdout.strip() or None


def send_to_remote_tmux(text, host, session, press_enter=False, timeout=10):
    """Send text to a remote tmux session via SSH.

    Args:
        text: Text to send.
        host: SSH host (e.g. user@ip).
        session: tmux session name.
        press_enter: If True, append Enter keypress.
        timeout: SSH command timeout in seconds.
    """
    escaped = text.replace("\\", "\\\\").replace("'", "'\\''").replace(";", "\\;")
    enter_part = " Enter" if press_enter else ""
    cmd = f"ssh {host} \"tmux send-keys -t {session} '{escaped}'{enter_part}\""
    subprocess.run(cmd, shell=True, capture_output=True, timeout=timeout)


def ensure_remote_dir(host, remote_dir, timeout=10):
    """Ensure a directory exists on the remote host."""
    subprocess.run(
        ["ssh", host, f"mkdir -p {remote_dir}"],
        capture_output=True, timeout=timeout,
    )


def scp_to_remote(local_path, host, remote_path, timeout=30):
    """Transfer a file to remote host via SCP. Returns True on success."""
    ret = subprocess.run(
        ["scp", "-q", local_path, f"{host}:{remote_path}"],
        capture_output=True, text=True, timeout=timeout,
    )
    return ret.returncode == 0
