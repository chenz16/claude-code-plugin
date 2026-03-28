#!/usr/bin/env python3
"""
Telegram bot for remotely monitoring and controlling Claude Code tmux sessions.

Usage:
    export TG_BOT_TOKEN="your-bot-token"
    export TG_USER_ID="your-telegram-user-id"
    python -m remote.tmux_bot

Prerequisites:
    pip install python-telegram-bot
    pip install funasr modelscope  # for voice messages
"""

import asyncio
import json
import logging
import os
import shlex
import sys
import tempfile

from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from shared.config import TG_BOT_TOKEN, TG_USER_ID, CLAUDE_CMD, DISPATCH_TIMEOUT, CAPTURE_LINES
from shared.tmux_utils import find_claude_instances, capture_pane, send_to_pane, sh
from shared.transcribe import transcribe_file

# ---------------------------------------------------------------------------
# Config validation
# ---------------------------------------------------------------------------

if not TG_BOT_TOKEN:
    sys.exit("Error: set TG_BOT_TOKEN environment variable")
if not TG_USER_ID:
    sys.exit("Error: set TG_USER_ID environment variable")

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    level=logging.INFO,
)
log = logging.getLogger(__name__)

# Focused terminal state: when set, all messages go directly to this terminal
_focused_target = None  # stores the tmux target string
_focused_project = None  # stores the project name for display
_focused_last_activity = 0  # timestamp of last message in focused mode
FOCUS_TIMEOUT = 1800  # auto-unfocus after 30 minutes of inactivity

# ---------------------------------------------------------------------------
# Dispatcher — uses `claude -p` to route user intent
# ---------------------------------------------------------------------------


def build_dispatch_prompt(user_message, instances):
    """Build the prompt for the routing LLM call."""
    summaries = []
    for i, inst in enumerate(instances, 1):
        screen = capture_pane(inst["target"], 25)
        screen_tail = screen[-800:] if len(screen) > 800 else screen
        summaries.append(
            f"Terminal #{i}: project={inst['project']}  cwd={inst['cwd']}  tmux={inst['target']}\n"
            f"Recent output:\n```\n{screen_tail}\n```"
        )

    return (
        "You are a dispatcher that manages multiple Claude Code terminal sessions for a user.\n"
        f"The user sent this message: \"{user_message}\"\n\n"
        "Here are the active terminals:\n\n"
        + "\n---\n".join(summaries)
        + "\n\n"
        "Your job:\n"
        "1. Determine which terminal(s) the user is referring to. If the message is about all terminals, set target to 0.\n"
        "2. Determine the action: \"peek\" (just read status) or \"send\" (type something into the terminal).\n"
        "3. If action is \"send\", determine what text to type. Be very careful - this gets typed directly into an interactive Claude Code session.\n"
        "4. Write a concise summary (1-2 sentences MAX) for the user in the same language they used. "
        "The user reads this on a phone via Telegram — be precise and actionable. "
        "Summarize WHAT the terminal is doing and WHETHER it succeeded/failed/is still running. "
        "Do NOT dump raw terminal output. Do NOT be verbose.\n\n"
        "Reply with ONLY valid JSON, no markdown fences, no explanation:\n"
        '{"target": <number, 0 for all>, "action": "peek" or "send", "command": "text to type if send, else empty string", "summary": "1-2 sentence status in user language"}'
    )


def dispatch(user_message, instances):
    """Call claude -p to route the user's message."""
    prompt = build_dispatch_prompt(user_message, instances)
    result = sh(
        f"echo {shlex.quote(prompt)} | {CLAUDE_CMD} -p",
        timeout=DISPATCH_TIMEOUT,
    )

    result = result.strip()
    if result.startswith("```"):
        lines = result.splitlines()
        result = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
    result = result.strip()
    if result.startswith("json"):
        result = result[4:].strip()

    try:
        return json.loads(result)
    except json.JSONDecodeError:
        log.warning("Failed to parse dispatch response: %s", result[:200])
        return {
            "target": 0,
            "action": "peek",
            "command": "",
            "summary": f"(routing failed, showing all terminals)\nRaw: {result[:200]}",
        }


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


def is_authorized(update):
    return update.effective_user is not None and update.effective_user.id == TG_USER_ID


# ---------------------------------------------------------------------------
# Telegram handlers
# ---------------------------------------------------------------------------


async def cmd_start(update, context):
    if not is_authorized(update):
        return
    await update.message.reply_text(
        "Claude Code Remote Monitor\n\n"
        "Commands:\n"
        "/list - show all active sessions\n"
        "/focus <n> - lock onto terminal #n (direct mode)\n"
        "/unfocus - back to AI routing mode\n"
        "/peek [n] - show terminal output\n"
        "/send <n> <text> - type text into terminal\n\n"
        "In focus mode: just type or speak, goes straight to the terminal.\n"
        "Auto-unfocus after 5 min idle."
    )


async def cmd_list(update, context):
    if not is_authorized(update):
        return

    instances = find_claude_instances()
    if not instances:
        await update.message.reply_text("No active Claude Code sessions found.")
        return

    lines = ["Active Claude Code sessions:\n"]
    for i, inst in enumerate(instances, 1):
        screen = capture_pane(inst["target"], 5)
        last_line = screen.strip().splitlines()[-1] if screen.strip() else "(empty)"
        lines.append(
            f"#{i}  {inst['project']}\n"
            f"    cwd: {inst['cwd']}\n"
            f"    tmux: {inst['target']}\n"
            f"    last: {last_line[:80]}"
        )
    await update.message.reply_text("\n\n".join(lines))


async def cmd_peek(update, context):
    if not is_authorized(update):
        return

    instances = find_claude_instances()
    if not instances:
        await update.message.reply_text("No active Claude Code sessions found.")
        return

    # If focused and no arg, peek the focused terminal
    if _focused_target and not context.args:
        output = capture_pane(_focused_target, CAPTURE_LINES)
        text = f"[{_focused_project}]\n\n```\n{output[-3500:]}\n```"
        await update.message.reply_text(text, parse_mode="Markdown")
        return

    try:
        idx = int(context.args[0]) - 1
        inst = instances[idx]
    except (IndexError, ValueError):
        await update.message.reply_text(f"Usage: /peek <1-{len(instances)}>")
        return

    output = capture_pane(inst["target"], CAPTURE_LINES)
    text = f"[{inst['project']}] ({inst['target']})\n\n```\n{output[-3500:]}\n```"
    await update.message.reply_text(text, parse_mode="Markdown")


async def cmd_send(update, context):
    if not is_authorized(update):
        return

    instances = find_claude_instances()
    if not instances:
        await update.message.reply_text("No active Claude Code sessions found.")
        return

    try:
        idx = int(context.args[0]) - 1
        inst = instances[idx]
        command = " ".join(context.args[1:])
        if not command:
            raise ValueError
    except (IndexError, ValueError):
        await update.message.reply_text(f"Usage: /send <1-{len(instances)}> <text>")
        return

    send_to_pane(inst["target"], command)
    await asyncio.sleep(2)
    output = capture_pane(inst["target"], CAPTURE_LINES)
    text = f"Sent to [{inst['project']}].\n\n```\n{output[-3500:]}\n```"
    await update.message.reply_text(text, parse_mode="Markdown")


async def cmd_focus(update, context):
    """Lock onto a specific terminal — all messages go directly there."""
    global _focused_target, _focused_project, _focused_last_activity
    if not is_authorized(update):
        return

    instances = find_claude_instances()
    if not instances:
        await update.message.reply_text("No active Claude Code sessions found.")
        return

    try:
        idx = int(context.args[0]) - 1
        inst = instances[idx]
    except (IndexError, ValueError):
        await update.message.reply_text(f"Usage: /focus <1-{len(instances)}>")
        return

    import time
    _focused_target = inst["target"]
    _focused_project = inst["project"]
    _focused_last_activity = time.time()
    output = capture_pane(_focused_target, 10)
    lines = output.strip().splitlines()
    trimmed = "\n".join(lines[-5:]) if len(lines) > 5 else output
    await update.message.reply_text(
        f"Focused on [{_focused_project}]\n"
        f"All messages now go directly to this terminal.\n"
        f"/unfocus to switch back to auto mode.\n\n"
        f"```\n{trimmed}\n```",
        parse_mode="Markdown",
    )


async def cmd_unfocus(update, context):
    """Release focus — go back to auto-dispatch mode."""
    global _focused_target, _focused_project
    if not is_authorized(update):
        return

    _focused_target = None
    _focused_project = None
    await update.message.reply_text("Unfocused. Messages will use AI routing again.")


async def handle_voice_text(update, context, user_msg):
    """Handle transcribed voice text — same as handle_text but with provided text."""
    if not is_authorized(update):
        return
    log.info("Voice message from %s: %s", update.effective_user.id, user_msg[:100])
    await _process_message(update, user_msg)


async def handle_text(update, context):
    if not is_authorized(update):
        return

    user_msg = update.message.text
    log.info("Message from %s: %s", update.effective_user.id, user_msg[:100])
    await _process_message(update, user_msg)


async def _process_message(update, user_msg):
    """Core message processing — used by both text and voice handlers.

    If focused on a terminal, sends directly without AI dispatch.
    Otherwise uses claude -p for intent routing.
    """
    global _focused_target, _focused_project, _focused_last_activity

    # Check focus timeout
    import time
    if _focused_target and (time.time() - _focused_last_activity > FOCUS_TIMEOUT):
        _focused_target = None
        _focused_project = None
        await update.message.reply_text(f"Auto-unfocused (idle > {FOCUS_TIMEOUT // 60}min). Using AI routing.")

    # Focused mode: send directly, no dispatch
    if _focused_target:
        # Check if focused terminal still exists
        output_before = capture_pane(_focused_target, 5)
        if output_before is None or output_before.startswith("[error"):
            _focused_target = None
            _focused_project = None
            await update.message.reply_text("Focused terminal no longer exists. Unfocused.")
            return

        _focused_last_activity = time.time()
        send_to_pane(_focused_target, user_msg)

        # Poll until Claude Code finishes (output stabilizes)
        await asyncio.sleep(3)
        last_output = ""
        stable_count = 0
        for _ in range(60):  # max ~2 minutes
            output = capture_pane(_focused_target, 20)
            if output == last_output:
                stable_count += 1
                if stable_count >= 3:  # stable for ~6 seconds = done
                    break
            else:
                stable_count = 0
                last_output = output
            await asyncio.sleep(2)

        # Send final result
        output = capture_pane(_focused_target, 20)
        lines = output.strip().splitlines()
        trimmed = "\n".join(lines[-10:]) if len(lines) > 10 else output
        reply = f"[{_focused_project}] Done.\n```\n{trimmed[-2000:]}\n```"
        try:
            await update.message.reply_text(reply, parse_mode="Markdown")
        except Exception:
            await update.message.reply_text(reply)
        return

    # Auto mode: dispatch via claude -p
    instances = find_claude_instances()
    if not instances:
        await update.message.reply_text("No active Claude Code sessions found.")
        return

    await update.message.reply_text("Checking...")
    decision = dispatch(user_msg, instances)
    log.info("Dispatch decision: %s", decision)

    target_idx = decision.get("target", 0)
    action = decision.get("action", "peek")
    command = decision.get("command", "")
    summary = decision.get("summary", "")

    if target_idx == 0:
        targets = list(enumerate(instances, 1))
    else:
        idx = max(0, min(target_idx - 1, len(instances) - 1))
        targets = [(idx + 1, instances[idx])]

    if action == "send" and command and len(targets) == 1:
        _, inst = targets[0]
        send_to_pane(inst["target"], command)
        await asyncio.sleep(3)

    # Build reply — summary first, raw output only for explicit peek
    if summary:
        reply = summary
    else:
        reply = ""

    # Only attach raw terminal output for peek actions or if no summary
    if action == "peek" or not summary:
        for num, inst in targets:
            output = capture_pane(inst["target"], CAPTURE_LINES)
            # Trim to last 10 lines for mobile readability
            lines = output.strip().splitlines()
            trimmed = "\n".join(lines[-10:]) if len(lines) > 10 else output
            max_output = 2000 // len(targets)
            reply += f"\n\n[#{num} {inst['project']}]\n```\n{trimmed[-max_output:]}\n```"

    if len(reply) > 4000:
        reply = reply[:4000] + "\n...(truncated)"

    try:
        await update.message.reply_text(reply, parse_mode="Markdown")
    except Exception:
        await update.message.reply_text(reply)


async def handle_voice(update, context):
    if not is_authorized(update):
        return

    voice = update.message.voice or update.message.audio
    if not voice:
        return

    file = await context.bot.get_file(voice.file_id)
    with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
        tmp_path = tmp.name
    await file.download_to_drive(tmp_path)

    try:
        text = transcribe_file(tmp_path)
    finally:
        os.unlink(tmp_path)

    if not text:
        await update.message.reply_text("Transcription failed.")
        return

    # In focus mode: skip "Heard" reply, just send directly
    if not _focused_target:
        await update.message.reply_text(f"Heard: {text}")
    await handle_voice_text(update, context, text)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    log.info("Starting Claude Code Telegram bot...")
    log.info("Authorized user ID: %s", TG_USER_ID)

    app = Application.builder().token(TG_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_start))
    app.add_handler(CommandHandler("list", cmd_list))
    app.add_handler(CommandHandler("peek", cmd_peek))
    app.add_handler(CommandHandler("send", cmd_send))
    app.add_handler(CommandHandler("focus", cmd_focus))
    app.add_handler(CommandHandler("unfocus", cmd_unfocus))
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, handle_voice))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    log.info("Bot is polling...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
