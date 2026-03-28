#!/bin/bash
# Setup voice-input or screenshot-input as a systemd user service (auto-start on boot)
#
# Usage:
#   bash scripts/setup_service.sh voice --host user@remote-ip
#   bash scripts/setup_service.sh screenshot --host user@remote-ip
#
# Prerequisites:
#   - Edit PYTHON_PATH below to match your system
#   - For voice: user must be in 'input' group, alsa-utils installed
#   - For screenshot: user must be in 'input' group, maim/scrot + xdotool installed

set -e

SERVICE_TYPE="${1:-voice}"
shift || true

PYTHON_PATH="${PYTHON_PATH:-$HOME/miniconda3/bin/python3}"
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
USER_ID=$(id -u)

if [ "$SERVICE_TYPE" = "voice" ]; then
    SERVICE_NAME="voice-input"
    DESCRIPTION="Voice Input for Claude Code"
    EXEC_START="${PYTHON_PATH} -m voice.voice_input $*"
elif [ "$SERVICE_TYPE" = "screenshot" ]; then
    SERVICE_NAME="screenshot-input"
    DESCRIPTION="Screenshot Input for Claude Code"
    EXEC_START="${PYTHON_PATH} -m screenshot.screenshot_input $*"
else
    echo "Usage: $0 <voice|screenshot> [args...]"
    echo ""
    echo "Examples:"
    echo "  $0 voice --host user@remote-ip"
    echo "  $0 screenshot --host user@remote-ip"
    echo "  $0 screenshot --hosts user@ip1,user@ip2"
    echo "  $0 screenshot --auto"
    exit 1
fi

mkdir -p ~/.config/systemd/user

cat > ~/.config/systemd/user/${SERVICE_NAME}.service << EOF
[Unit]
Description=${DESCRIPTION}
After=graphical-session.target

[Service]
WorkingDirectory=${REPO_DIR}
ExecStart=${EXEC_START}
Restart=on-failure
RestartSec=5
Environment=DISPLAY=:0
Environment=XDG_RUNTIME_DIR=/run/user/${USER_ID}
Environment=HOME=${HOME}

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload
systemctl --user enable ${SERVICE_NAME}.service
systemctl --user start ${SERVICE_NAME}.service

# Enable lingering so service starts on boot even without login
loginctl enable-linger $USER

echo ""
echo "Service '${SERVICE_NAME}' installed and started!"
echo "Check status: systemctl --user status ${SERVICE_NAME}.service"
echo "View logs:    journalctl --user -u ${SERVICE_NAME}.service -f"
echo "Restart:      systemctl --user restart ${SERVICE_NAME}.service"
echo "Stop:         systemctl --user stop ${SERVICE_NAME}.service"
