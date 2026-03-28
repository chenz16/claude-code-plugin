# Claude Code Plugin

Voice input, screenshot sharing, and remote access tools for Claude Code.

## Problems We Solve

### 1. No Mandarin Voice Input
Claude Code's built-in voice input does not support Mandarin. For Chinese-speaking developers, this means typing everything — switching between Chinese and English input methods constantly.

**Solution:** `claude-voice` uses Alibaba's SenseVoiceSmall model locally. Best-in-class Mandarin recognition, runs offline, no API fees. Hold Right Alt, speak, release — text appears in Claude Code.

### 2. Screenshot Sharing Is Painful (Especially Remote)
Claude Code can read images, but getting a screenshot to it is a multi-step nightmare: take screenshot → save file → scp to remote → type the path → press Enter. On remote servers via SSH, there's no clipboard bridge at all.

**Solution:** `claude-screenshot` monitors your clipboard automatically. Take a screenshot with any tool (Win+Shift+S, Cmd+Shift+4, etc.), and it auto-detects where Claude Code is running (local, WSL, or remote) and sends the image there. Or just say "帮我看看这张截图" via voice — the plugin grabs the screenshot and sends it along with your voice command.

### 3. Cross-Platform Setup Is Complicated
Getting voice input and remote access working across Windows, WSL, macOS, and Linux involves different audio systems, keyboard APIs, and network configurations. WSL adds extra complexity with port forwarding and audio device bridging.

**Solution:** `claude-plugin` auto-detects your platform and picks the right backend. One `pip install` command, one unified CLI. WSL port forwarding is set up automatically. No manual configuration needed.

### 4. Telegram Remote Access Is Slow
Using a Telegram bot for remote Claude Code control means every message goes through Telegram's servers (overseas), adding 2-10 seconds of latency. Voice messages are even slower.

**Solution:** `claude-remote --web` runs a private WebSocket server on your machine. Phone connects directly over LAN — zero cloud latency. Voice recording happens in the browser, SenseVoice runs on your server, results stream back in real-time.

### 5. Privacy — Everything Should Stay Private
Cloud-based voice recognition, third-party bots, and public relay servers all mean your code and conversations pass through someone else's infrastructure.

**Solution:** Everything runs locally. SenseVoice model runs on your machine (no API calls). The web server is your own private server — data goes directly between your phone and your machine, never touches any cloud. Even remote access via Tailscale is a direct encrypted peer-to-peer tunnel.

### 6. Device Auto-Detection
Manually specifying which terminal, which server, which tmux session to send to is tedious. The tool should just know.

**Solution:** Auto-detection everywhere. `claude-voice` detects Windows/Mac/WSL/Linux and picks the right mode. `claude-screenshot` detects if Claude Code is in WSL tmux, SSH remote, or local — and sends there automatically. `claude-remote --web` auto-detects your LAN IP, generates QR code, sets up port forwarding, and creates HTTPS certificates — all automatically.

---

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

## Usage

All commands available through `claude-plugin`:

```bash
claude-plugin voice              # Voice input
claude-plugin screenshot         # Screenshot clipboard monitor
claude-plugin remote --web       # Phone remote control (private web server)
claude-plugin remote             # Telegram bot
claude-plugin status             # Show running services
claude-plugin help               # Help
```

Individual commands also work: `claude-voice`, `claude-screenshot`, `claude-remote`.

### Voice Input

Hold Right Alt to speak, release to transcribe. Auto-detects platform.

```bash
claude-plugin voice                        # local paste (Windows/Mac/WSL)
claude-plugin voice --host user@remote-ip  # send to remote server
```

**Voice + Screenshot:** Say "帮我看看这张截图" or "look at this screenshot" — the plugin auto-grabs the latest screenshot from clipboard and sends it to Claude Code with your voice text.

| Platform | Audio | Keyboard | Output |
|----------|-------|----------|--------|
| Windows | sounddevice | pynput | Ctrl+V paste |
| macOS | sounddevice | pynput | Cmd+V paste / SSH remote |
| WSL | sounddevice | pynput | local paste / SSH remote |
| Linux | arecord (ALSA) | evdev | SSH + tmux send-keys |

### Screenshot Input

Monitors clipboard. Use any screenshot tool you like.

```bash
claude-plugin screenshot                     # auto-detect target
claude-plugin screenshot --wsl               # force WSL tmux
claude-plugin screenshot --host user@ip      # force remote
```

### Phone Remote Control

Private web server with real-time voice and text.

```bash
claude-plugin remote --web
```

- QR code in terminal — scan to connect
- HTTPS auto-generated (required for mobile mic)
- Press-and-hold mic button, like WeChat voice
- Real-time terminal output streaming
- Add to phone home screen as app (PWA)

**Commands (web UI):**
- `/list` — show Claude Code sessions
- `/focus <n>` — lock onto terminal #n (direct mode)
- `/unfocus` — back to AI routing
- `/peek [n]` — view terminal output
- Just type or speak — goes straight to focused terminal

Auto-unfocus after 30 min idle. Messages from mobile auto-tagged `[M]` for concise Claude Code output.

### Telegram Bot (backup)

```bash
cp remote/.env.example remote/.env  # add TG_BOT_TOKEN and TG_USER_ID
claude-plugin remote
```

## Remote Access Outside LAN

### Tailscale (recommended)

Access from anywhere — coffee shop, mobile data, through corporate VPNs:

```bash
# Server
curl -fsSL https://tailscale.com/install.sh | sudo sh
sudo tailscale up
tailscale ip  # → 100.x.x.x

# Phone: install Tailscale app, login same account
# Access: https://100.x.x.x:8080
```

Direct encrypted tunnel. Data never goes through any cloud.

### WSL Port Forwarding

Auto-configured on startup. Manual setup if needed (Windows PowerShell Admin):

```powershell
netsh interface portproxy add v4tov4 listenport=8080 listenaddress=0.0.0.0 connectport=8080 connectaddress=<WSL-IP>
New-NetFirewallRule -DisplayName "Claude Code Remote" -Direction Inbound -LocalPort 8080 -Protocol TCP -Action Allow
```

## Platform Support

| Tool | Windows | macOS | WSL | Linux |
|------|---------|-------|-----|-------|
| `claude-plugin voice` | local paste | local paste / SSH | local paste / SSH | SSH remote |
| `claude-plugin screenshot` | local / WSL / remote | local / remote | via Windows | local / remote |
| `claude-plugin remote --web` | full | full | full | full |
| `claude-plugin remote` (Telegram) | full | full | full | full |

## Project Structure

```
claude-code-plugin/
├── cli.py               # Unified entry point (claude-plugin)
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
│   ├── web_server.py    # Private WebSocket server
│   └── tmux_bot.py      # Telegram bot
└── scripts/             # Systemd service installer
```

## License

MIT
