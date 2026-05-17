"""Telegram forum supergroup: workspace link + per-agent topics."""

from __future__ import annotations

import logging
import re
from uuid import UUID

import asyncpg
from telegram import Bot
from telegram.constants import ChatType
from telegram.error import TelegramError

from personal_ai_os.db import queries
from personal_ai_os.db.models import AgentRow, UserRow

logger = logging.getLogger(__name__)

_MAX_TOPIC_NAME = 128

WORKSPACE_SETUP_TEXT = """**Рабочее пространство (топики агентов)**

1. Создай **супергруппу** в Telegram (New Group → добавь себя).
2. Настройки группы → **Topics** (Темы) → включить.
3. Добавь этого бота в группу.
4. Сделай бота **администратором** с правом **Manage Topics** (Управление темами).
5. В группе отправь команду: `/link_workspace`

После привязки: `/topic <имя агента>` — бот создаст топик, изолированный под этого агента.
Пиши в топике — отвечает только он (без смешения контекста с другими агентами).

Общая тема (General) — оркестратор Meta (как в личке)."""


def _sanitize_topic_name(name: str) -> str:
    s = re.sub(r"\s+", " ", (name or "Агент").strip())
    if len(s) > _MAX_TOPIC_NAME:
        s = s[: _MAX_TOPIC_NAME - 1] + "…"
    return s or "Агент"


async def link_workspace(
    conn: asyncpg.Connection,
    user: UserRow,
    chat_id: int,
    *,
    is_forum: bool,
) -> str:
    if not is_forum:
        return (
            "В этой группе не включены **Темы** (Forum). "
            "Настройки группы → Topics → включить, затем снова `/link_workspace`."
        )
    await queries.set_user_workspace(conn, user.id, chat_id)
    return (
        f"Рабочее пространство привязано (chat_id `{chat_id}`).\n\n"
        "Создать топик агента: `/topic <имя>` или «открой тему для Память».\n"
        "Список топиков: `/topics`"
    )


async def create_topic_for_agent(
    bot: Bot,
    conn: asyncpg.Connection,
    user: UserRow,
    agent: AgentRow,
    *,
    title: str | None = None,
) -> tuple[str, int | None]:
    """Create forum topic and bind to agent. Returns (message, thread_id)."""
    ws = await queries.get_user_workspace_chat_id(conn, user.id)
    if ws is None:
        return WORKSPACE_SETUP_TEXT, None

    existing = await queries.get_active_topic_for_agent(conn, user.id, agent.id)
    if existing is not None:
        return (
            f"У агента «{agent.name}» уже есть топик «{existing.topic_title}» "
            f"(thread_id `{existing.telegram_thread_id}`). Пиши туда.\n"
            "Новый топик: сначала `/topic_archive {agent.name}` (позже) или другой агент.",
        ), existing.telegram_thread_id

    topic_name = _sanitize_topic_name(title or agent.name)
    try:
        topic = await bot.create_forum_topic(chat_id=ws, name=topic_name)
    except TelegramError as e:
        logger.exception("create_forum_topic failed chat=%s", ws)
        err = str(e).lower()
        if "not enough rights" in err or "admin" in err:
            return (
                "Не хватает прав: сделай бота **администратором** с **Manage Topics**, "
                "затем повтори `/topic`."
            ), None
        return f"Не удалось создать топик: {e}", None

    thread_id = topic.message_thread_id
    await queries.insert_agent_topic(
        conn,
        user_id=user.id,
        agent_id=agent.id,
        workspace_chat_id=ws,
        telegram_thread_id=thread_id,
        topic_title=topic_name,
    )

    welcome = (
        f"Топик агента **{agent.name}** (`{agent.agent_type}`).\n"
        f"ID агента: `{agent.id}`\n\n"
        "Здесь изолированный контекст только для этого агента. "
        "Общие вопросы и оркестрация — в теме General или в личке с ботом."
    )
    try:
        await bot.send_message(
            chat_id=ws,
            text=welcome,
            message_thread_id=thread_id,
        )
    except TelegramError:
        logger.exception("welcome message to topic failed")

    return (
        f"Создан топик **{topic_name}** для агента «{agent.name}».\n"
        f"Перейди в группу и пиши в этот топик — ответит только он."
    ), thread_id


async def resolve_agent_for_update(
    conn: asyncpg.Connection,
    chat_id: int,
    thread_id: int | None,
    telegram_user_id: int,
) -> tuple[UserRow | None, AgentRow | None, bool]:
    """
    Returns (user, agent, is_general_or_dm).
    is_general_or_dm True → use Meta in workspace General or unknown thread.
    """
    user = await queries.get_user_by_workspace_chat(conn, chat_id)
    if user is None or user.telegram_id != telegram_user_id:
        return None, None, True

    if thread_id is None:
        return user, None, True

    topic = await queries.get_agent_topic_by_thread(conn, chat_id, thread_id)
    if topic is None:
        return user, None, True

    agent = await queries.get_agent(conn, user.id, topic.agent_id)
    if agent is None or not agent.is_active:
        return user, None, True

    return user, agent, False
