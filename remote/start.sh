#!/bin/bash
# Quick start script for Claude Code Telegram bot
# Usage: ./start.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
BOT_SCRIPT="$SCRIPT_DIR/tmux_bot.py"
CONFIG_FILE="$SCRIPT_DIR/.env"

# Load config if exists
if [ -f "$CONFIG_FILE" ]; then
    set -a
    source "$CONFIG_FILE"
    set +a
fi

# Check required env vars
if [ -z "$TG_BOT_TOKEN" ]; then
    echo "Error: TG_BOT_TOKEN not set."
    echo "Either export it or create $CONFIG_FILE with:"
    echo '  TG_BOT_TOKEN="your-bot-token"'
    echo '  TG_USER_ID="your-user-id"'
    exit 1
fi

if [ -z "$TG_USER_ID" ]; then
    echo "Error: TG_USER_ID not set."
    exit 1
fi

# Check dependencies
if ! python3 -c "import telegram" 2>/dev/null; then
    echo "Installing python-telegram-bot..."
    pip install python-telegram-bot --break-system-packages
fi

if ! python3 -c "import funasr" 2>/dev/null; then
    echo "Installing funasr + modelscope (SenseVoice for voice messages)..."
    pip install funasr modelscope --break-system-packages
fi

# Check tmux is running
if ! tmux list-sessions &>/dev/null; then
    echo "Warning: no tmux sessions found. The bot will report no instances."
fi

echo "Starting Claude Code Telegram bot..."
echo "  Bot token: ${TG_BOT_TOKEN:0:10}..."
echo "  User ID:   $TG_USER_ID"
echo ""

cd "$REPO_DIR"
exec python3 -m remote.tmux_bot
