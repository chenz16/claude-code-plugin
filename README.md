# Claude Code Plugin

Voice input, screenshot sharing, and remote access tools for Claude Code.

## Quick Install

### Windows (PowerShell)

```powershell
pip install "claude-code-plugin[windows] @ git+https://github.com/chenz16/claude-code-plugin.git"
```

### macOS (Terminal)

```bash
pip install "git+https://github.com/chenz16/claude-code-plugin.git[macos]"
```

### Linux

```bash
sudo apt install alsa-utils maim xdotool
sudo usermod -aG input $USER  # log out/in after
pip install "git+https://github.com/chenz16/claude-code-plugin.git[linux]"
```

### WSL (Ubuntu on Windows)

```bash
sudo apt install libportaudio2 ffmpeg
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

Voice + screenshot integration: say "look at this screenshot" (or Chinese equivalents like "帮我看看这张截图") and the plugin auto-grabs the latest screenshot from clipboard and sends it to Claude Code along with your voice text.

| Platform | Audio | Keyboard | Output |
|----------|-------|----------|--------|
| Windows | sounddevice | pynput | Ctrl+V paste |
| macOS | sounddevice | pynput | Cmd+V paste / SSH remote |
| WSL | sounddevice | pynput | local paste / SSH remote |
| Linux | arecord (ALSA) | evdev | SSH + tmux send-keys |

### `claude-screenshot` — Screenshot Input (All Platforms)

Monitors your clipboard for new screenshots. Use any screenshot tool you like — the plugin detects it automatically.

```bash
# Auto-detect: WSL tmux → SSH → local paste
claude-screenshot

# Force send to WSL tmux
claude-screenshot --wsl

# Force send to remote server
claude-screenshot --host user@remote-ip
```

| Platform | Screenshot tool | How it works |
|----------|----------------|-------------|
| Windows | `Win+Shift+S` (or any) | Clipboard monitoring, auto-detect target |
| macOS | `Cmd+Shift+4` (or any) | Clipboard monitoring |
| Linux | Flameshot, PrintScreen, etc. | Clipboard monitoring |

### `claude-remote` — Remote Access (All Platforms)

Two modes: private local web server (fast) or Telegram bot (anywhere).

#### Local Web Server (recommended)

Zero-latency private server. Access from phone browser.

```bash
claude-remote --web
```

- QR code shown in terminal — scan with phone to connect
- HTTPS with auto-generated certificate (required for mobile mic)
- Voice recording in browser + SenseVoice on server
- Real-time output streaming from Claude Code
- Add to phone home screen for app-like experience (PWA)

**Commands (in web UI or Telegram):**
- `/list` — show all active Claude Code sessions
- `/focus <n>` — lock onto terminal #n (direct mode, all messages go there)
- `/unfocus` — back to AI routing mode
- `/peek [n]` — view terminal output
- `/send <n> <text>` — type text into terminal

In focus mode: just type or speak, goes straight to the terminal. Auto-unfocus after 30 min idle.

#### Telegram Bot

For access via Telegram (higher latency, works anywhere).

```bash
# Set up config
cp remote/.env.example remote/.env
# Edit with your TG_BOT_TOKEN and TG_USER_ID

claude-remote
```

## Remote Access Outside LAN

### Tailscale (recommended)

Access your server from anywhere (coffee shop, mobile data, etc.) with zero configuration:

```bash
# On your server (WSL/Linux)
curl -fsSL https://tailscale.com/install.sh | sudo sh
sudo tailscale up
tailscale ip  # note your Tailscale IP (100.x.x.x)
```

On your phone: install Tailscale app (free, App Store / Google Play), login with same account.

Then access from anywhere:
```
https://<tailscale-ip>:8080
```

Tailscale creates a direct encrypted tunnel between devices. Data never goes through any cloud server. Works through corporate VPNs and firewalls.

### WSL Port Forwarding

For LAN access from WSL, port forwarding is set up automatically. If manual setup is needed (run in Windows PowerShell as Admin):

```powershell
# Get WSL IP
wsl hostname -I

# Set up port forwarding
netsh interface portproxy add v4tov4 listenport=8080 listenaddress=0.0.0.0 connectport=8080 connectaddress=<WSL-IP>

# Allow through firewall
New-NetFirewallRule -DisplayName "Claude Code Remote" -Direction Inbound -LocalPort 8080 -Protocol TCP -Action Allow
```

## Mobile Mode

Messages from the web UI are prefixed with `[M]` tag. Add this to your project's CLAUDE.md to enable concise mobile-friendly output:

```markdown
When a message starts with [M], keep responses short (2-3 sentences),
no tables, no box-drawing characters, no decorative separators.
```

## Platform Support

| Tool | Windows | macOS | WSL | Linux |
|------|---------|-------|-----|-------|
| `claude-voice` | local paste | local paste / SSH remote | local paste / SSH remote | SSH remote |
| `claude-screenshot` | local / WSL / remote | local / remote | via Windows | local / remote |
| `claude-remote --web` | full support | full support | full support | full support |
| `claude-remote` (Telegram) | full support | full support | full support | full support |

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
├── shared/              # Common modules
│   ├── config.py        # Centralized settings
│   ├── transcribe.py    # SenseVoice speech-to-text
│   ├── clipboard_image.py # Screenshot intent detection
│   ├── hotkey.py        # evdev global keyboard
│   ├── ssh_remote.py    # SSH + remote tmux
│   └── tmux_utils.py    # Local tmux discovery
├── voice/               # Voice input (all platforms)
├── screenshot/          # Screenshot input (all platforms)
├── remote/              # Remote access
│   ├── web_server.py    # Local WebSocket server (fast, private)
│   └── tmux_bot.py      # Telegram bot
└── scripts/             # Systemd service installer
```

## Speech Model

Uses [SenseVoiceSmall](https://github.com/FunAudioLLM/SenseVoice) by Alibaba FunAudioLLM:
- Best-in-class Mandarin + Cantonese recognition
- Also supports English, Japanese, Korean
- ~1GB model, runs fully offline after first download

## License

MIT
