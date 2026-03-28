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
        "4. Write a brief summary for the user in the same language they used.\n\n"
        "Reply with ONLY valid JSON, no markdown fences, no explanation:\n"
        '{"target": <number, 0 for all>, "action": "peek" or "send", "command": "text to type if send, else empty string", "summary": "brief status in user language"}'
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
        "Send any message to check on your Claude Code sessions.\n"
        "Commands:\n"
        "/list - show all active sessions\n"
        "/peek <n> - show terminal #n output\n"
        "/send <n> <text> - type text into terminal #n\n"
        "Or just describe what you want in natural language."
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


async def handle_text(update, context):
    if not is_authorized(update):
        return

    user_msg = update.message.text
    log.info("Message from %s: %s", update.effective_user.id, user_msg[:100])

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

    parts = []
    if summary:
        parts.append(summary)

    for num, inst in targets:
        output = capture_pane(inst["target"], CAPTURE_LINES)
        max_output = 3000 // len(targets)
        parts.append(
            f"\n[#{num} {inst['project']}]\n```\n{output[-max_output:]}\n```"
        )

    reply = "\n".join(parts)
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

    await update.message.reply_text(f"Heard: {text}")
    update.message.text = text
    await handle_text(update, context)


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
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, handle_voice))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    log.info("Bot is polling...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
