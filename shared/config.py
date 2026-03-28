"""
Shared configuration for all modules.

Centralizes common settings: SSH, tmux, audio, model paths.
Each module can extend with its own specific config.
"""

import os

# ── Audio ──
SAMPLE_RATE = 16000
CHANNELS = 1
BLOCK_SIZE = int(SAMPLE_RATE * 0.1)  # 100ms per block
MIN_DURATION = 0.3  # minimum recording duration in seconds
TMPWAV = "/tmp/whisper_input.wav"

# ── SenseVoice Model ──
MODEL_ID = "iic/SenseVoiceSmall"
DEFAULT_LANGUAGE = "zh"

# ── tmux ──
CAPTURE_LINES = int(os.environ.get("CAPTURE_LINES", "40"))

# ── Screenshot ──
SCREENSHOT_LOCAL_DIR = "/tmp/claude-screenshots"
SCREENSHOT_REMOTE_DIR = "/tmp/claude-screenshots"

# ── Remote Access (Telegram) ──
TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN", "")
TG_USER_ID = int(os.environ.get("TG_USER_ID", "0") or "0")
CLAUDE_CMD = os.environ.get("CLAUDE_CMD", "claude")
DISPATCH_TIMEOUT = int(os.environ.get("DISPATCH_TIMEOUT", "60"))

# ── Platform detection ──
IS_WSL = "microsoft" in os.uname().release.lower() if hasattr(os, "uname") else False
