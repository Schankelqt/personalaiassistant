"""Helpers for replies in forum topics and groups."""

from __future__ import annotations

from typing import Any

from telegram import Update
from telegram.ext import ContextTypes


def reply_kwargs(update: Update) -> dict[str, Any]:
    """Pass message_thread_id so replies stay in the same forum topic."""
    msg = update.effective_message
    if msg and msg.message_thread_id is not None:
        return {"message_thread_id": msg.message_thread_id}
    return {}


async def detect_forum_chat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    chat = update.effective_chat
    msg = update.effective_message
    if chat is None:
        return False
    if getattr(chat, "is_forum", False):
        return True
    if msg and getattr(msg, "is_topic_message", False):
        return True
    if msg and msg.message_thread_id is not None:
        return True
    try:
        full = await context.bot.get_chat(chat.id)
        return bool(getattr(full, "is_forum", False))
    except Exception:
        return False
