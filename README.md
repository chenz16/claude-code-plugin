# Claude Code Plugin

A toolkit for enhancing Claude Code, especially on remote servers via SSH + tmux.

## Modules

### Voice Input (`voice/`)
Mandarin speech recognition for Claude Code using Alibaba SenseVoice.
- `voice_input.py` — Linux + evdev, sends to remote tmux via SSH
- `voice_input_linux.py` — Linux/WSL, local paste or remote SSH mode
- `voice_input_win.py` — Windows, local paste via clipboard

**Usage:**
```bash
python -m voice.voice_input --host user@remote-ip
python -m voice.voice_input_linux                     # local paste
python -m voice.voice_input_linux --host user@remote  # remote
```

### Screenshot Input (`screenshot/`)
One-hotkey screenshot capture and transfer to remote Claude Code.
- PrintScreen → full screen
- Right Ctrl → region selection

**Usage:**
```bash
python -m screenshot.screenshot_input --host user@remote-ip
python -m screenshot.screenshot_input --hosts user@ip1,user@ip2
python -m screenshot.screenshot_input --auto
```

### Remote Access (`remote/`)
Telegram bot for monitoring and controlling Claude Code sessions remotely.
- Natural language routing via `claude -p`
- Voice message transcription
- Multi-instance management

**Usage:**
```bash
cp remote/.env.example remote/.env  # edit with your tokens
bash remote/start.sh
```

## Shared (`shared/`)

Common code extracted from all modules:

| Module | Description | Used by |
|--------|-------------|---------|
| `config.py` | Centralized settings (audio, tmux, SSH, etc.) | All |
| `transcribe.py` | SenseVoice speech-to-text | voice, remote |
| `hotkey.py` | evdev global keyboard detection | voice, screenshot |
| `ssh_remote.py` | SSH connection, remote tmux operations | voice, screenshot |
| `tmux_utils.py` | Local tmux instance discovery and control | remote |

## Installation

### Linux (voice + screenshot + remote)
```bash
sudo apt install alsa-utils maim xdotool
sudo usermod -aG input $USER  # log out/in after
pip install -r requirements_linux.txt
```

### Windows (voice only)
```powershell
pip install -r requirements_win.txt
```

## Auto-start as systemd service

```bash
# Voice input
bash scripts/setup_service.sh voice --host user@remote-ip

# Screenshot input
bash scripts/setup_service.sh screenshot --host user@remote-ip
```

## License

MIT
