"""Assemble context for Meta / onboarding: user request → context block → AI."""

from __future__ import annotations

from uuid import UUID

import asyncpg
from redis.asyncio import Redis

from personal_ai_os.core.context_helpers import format_people_for_prompt
from personal_ai_os.core.session_store import get_messages
from personal_ai_os.db import queries
from personal_ai_os.db.models import AgentRow, UserRow
from personal_ai_os.services.knowledge_base import format_entries_block, search_relevant


async def build_meta_context(
    conn: asyncpg.Connection,
    redis: Redis,
    user: UserRow,
    user_message: str,
    agents: list[AgentRow],
) -> str:
    """Context pack injected into Meta-Agent system prompt."""
    kb_hits = await search_relevant(conn, user.id, user_message, limit=8)
    profile_entries = await queries.list_knowledge_by_category(
        conn, user.id, categories=("profile", "preferences")
    )

    agents_lines = "\n".join(
        f"- id={a.id} type={a.agent_type} name={a.name}" for a in agents if a.is_active
    )
    people = await queries.list_people(conn, user.id)
    people_ctx = format_people_for_prompt(people)

    session_tail = await get_messages(redis, user.id)
    prior = "\n".join(f"{m['role']}: {m['content']}" for m in session_tail[-12:])

    ws = await queries.get_user_workspace_chat_id(conn, user.id)
    workspace_line = (
        f"Рабочая группа привязана (chat_id={ws})."
        if ws
        else "Рабочая группа не привязана — подскажи /workspace и /link_workspace."
    )

    parts = [
        "## Профиль (база знаний)",
        format_entries_block(profile_entries, header=""),
        "## Релевантные знания по запросу",
        format_entries_block(kb_hits, header=""),
        "## Агенты пользователя",
        agents_lines or "(нет)",
        "## Память: люди",
        people_ctx,
        "## Рабочее пространство",
        workspace_line,
        "## Последние реплики (личка с Meta)",
        prior or "(пусто)",
    ]
    return "\n\n".join(parts)


async def build_onboarding_context(
    conn: asyncpg.Connection,
    user_message: str,
) -> str:
    system_docs = await queries.list_system_knowledge(
        conn, categories=("onboarding", "product")
    )
    return format_entries_block(
        system_docs,
        header="Справка для ведения онбординга:",
    )
