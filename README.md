# Claude Code Plugin

Voice input, screenshot sharing, and remote access tools for Claude Code.

## Quick Install

### Windows (PowerShell)

```powershell
pip install "git+https://github.com/chenz16/claude-code-plugin.git[windows]"
```

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

Auto-detects your platform (Windows / WSL / Linux) and runs the right mode.

```bash
# Windows: hold Right Alt to speak, release to paste into focused window
claude-voice

# WSL: same as Windows, local paste mode
claude-voice

# Linux: send voice to remote Claude Code via SSH + tmux
claude-voice --host user@remote-ip
```

| Platform | Audio | Keyboard | Output |
|----------|-------|----------|--------|
| Windows | sounddevice | pynput | clipboard paste (Ctrl+V) |
| WSL | sounddevice | pynput | local paste or SSH remote |
| Linux | arecord (ALSA) | evdev | SSH + tmux send-keys |

### `claude-screenshot` — Screenshot Input (Linux only)

Capture screenshots and send to remote Claude Code with one hotkey.

```bash
# Single host
claude-screenshot --host user@remote-ip

# Multi-host: auto-detect from focused terminal window
claude-screenshot --hosts user@ip1,user@ip2

# Full auto: scan all active SSH connections
claude-screenshot --auto
```

**Hotkeys:**
- `PrintScreen` → full screen capture
- `Right Ctrl` → region selection

### `claude-remote` — Remote Access (Windows & Linux)

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

| Tool | Windows | WSL | Linux |
|------|---------|-----|-------|
| `claude-voice` | local paste | local paste / SSH remote | SSH remote |
| `claude-screenshot` | - | - | full support |
| `claude-remote` | full support | full support | full support |

## Auto-start as systemd service (Linux)

```bash
# Voice input
bash scripts/setup_service.sh voice --host user@remote-ip

# Screenshot input
bash scripts/setup_service.sh screenshot --host user@remote-ip
```

Manage services:
```bash
systemctl --user status voice-input.service
journalctl --user -u voice-input.service -f
systemctl --user restart voice-input.service
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
├── screenshot/       # Screenshot input (Linux)
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
