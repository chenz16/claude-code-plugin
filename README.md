# Claude Code Plugin

Voice input, screenshot sharing, and remote access tools for Claude Code.

## Quick Install

### Windows (PowerShell)

```powershell
pip install "git+https://github.com/chenz16/claude-code-plugin.git[windows]"
```

### macOS (Terminal)

```bash
pip install "git+https://github.com/chenz16/claude-code-plugin.git[macos]"
```

> macOS may prompt for Accessibility permission (needed for global hotkeys). Go to System Settings > Privacy & Security > Accessibility and allow your terminal app.

### Linux

```bash
sudo apt install alsa-utils maim xdotool
sudo usermod -aG input $USER  # log out/in after
pip install "git+https://github.com/chenz16/claude-code-plugin.git[linux]"
```

### WSL (Ubuntu on Windows)

```bash
sudo apt install libportaudio2
pip install "git+https://github.com/chenz16/claude-code-plugin.git[linux]"
```

> First run downloads the SenseVoice model (~1GB). After that it starts instantly.

## Commands

### `claude-voice` — Voice Input

Hold Right Alt to speak, release to transcribe. Auto-detects your platform.

```bash
# Windows / macOS / WSL: local paste mode
claude-voice

# Linux / macOS: send to remote Claude Code via SSH
claude-voice --host user@remote-ip
```

| Platform | Audio | Keyboard | Output |
|----------|-------|----------|--------|
| Windows | sounddevice | pynput | Ctrl+V paste |
| macOS | sounddevice | pynput | Cmd+V paste / SSH remote |
| WSL | sounddevice | pynput | local paste / SSH remote |
| Linux | arecord (ALSA) | evdev | SSH + tmux send-keys |

### `claude-screenshot` — Screenshot Input (Linux & macOS)

Capture screenshots and send to remote Claude Code with one hotkey.

```bash
# Single host
claude-screenshot --host user@remote-ip

# Multi-host: auto-detect from focused terminal window
claude-screenshot --hosts user@ip1,user@ip2

# Full auto: scan all active SSH connections
claude-screenshot --auto
```

| Platform | Hotkeys | Capture tool |
|----------|---------|-------------|
| Linux | `PrintScreen` = full, `Right Ctrl` = region | maim / scrot |
| macOS | `Ctrl+Shift+3` = full, `Ctrl+Shift+4` = region | screencapture (built-in) |

### `claude-remote` — Remote Access (All Platforms)

Telegram bot for monitoring and controlling Claude Code sessions.

```bash
# Set up config
cp remote/.env.example remote/.env
# Edit remote/.env with your TG_BOT_TOKEN and TG_USER_ID

# Run
claude-remote
```

**Bot commands:**
- `/list` — show all active Claude Code sessions
- `/peek <n>` — view terminal #n output
- `/send <n> <text>` — type text into terminal #n
- Natural language — AI-powered routing via `claude -p`
- Voice messages — auto-transcribed with SenseVoice

## Platform Support

| Tool | Windows | macOS | WSL | Linux |
|------|---------|-------|-----|-------|
| `claude-voice` | local paste | local paste / SSH remote | local paste / SSH remote | SSH remote |
| `claude-screenshot` | - | full support | - | full support |
| `claude-remote` | full support | full support | full support | full support |

## Auto-start as systemd service (Linux)

```bash
# Voice input
bash scripts/setup_service.sh voice --host user@remote-ip

# Screenshot input
bash scripts/setup_service.sh screenshot --host user@remote-ip
```

## Project Structure

```
claude-code-plugin/
├── shared/           # Common modules
│   ├── config.py     # Centralized settings
│   ├── transcribe.py # SenseVoice speech-to-text
│   ├── hotkey.py     # evdev global keyboard
│   ├── ssh_remote.py # SSH + remote tmux
│   └── tmux_utils.py # Local tmux discovery
├── voice/            # Voice input (all platforms)
├── screenshot/       # Screenshot input (Linux & macOS)
├── remote/           # Remote access (Telegram bot)
└── scripts/          # Systemd service installer
```

## Speech Model

Uses [SenseVoiceSmall](https://github.com/FunAudioLLM/SenseVoice) by Alibaba FunAudioLLM:
- Best-in-class Mandarin + Cantonese recognition
- Also supports English, Japanese, Korean
- ~1GB model, runs fully offline after first download

## License

MIT
