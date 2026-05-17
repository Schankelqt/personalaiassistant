"""Knowledge base read/write and prompt formatting."""

from __future__ import annotations

import re
from typing import Any
from uuid import UUID

import asyncpg

from personal_ai_os.db import queries
from personal_ai_os.db.models import KnowledgeEntryRow


def _query_tokens(text: str, *, max_tokens: int = 6) -> list[str]:
    words = re.findall(r"[\wа-яА-ЯёЁ]{3,}", (text or "").lower())
    seen: set[str] = set()
    out: list[str] = []
    for w in words:
        if w in seen:
            continue
        seen.add(w)
        out.append(w)
        if len(out) >= max_tokens:
            break
    return out


async def search_relevant(
    conn: asyncpg.Connection,
    user_id: UUID,
    query: str,
    *,
    limit: int = 8,
) -> list[KnowledgeEntryRow]:
    return await queries.search_knowledge(conn, user_id, query, limit=limit)


async def upsert_user_fact(
    conn: asyncpg.Connection,
    user_id: UUID,
    *,
    category: str,
    title: str,
    content: str,
    tags: list[str] | None = None,
) -> KnowledgeEntryRow:
    return await queries.upsert_knowledge_entry(
        conn,
        user_id=user_id,
        category=category,
        title=title,
        content=content,
        tags=tags or [],
    )


def format_entries_block(entries: list[KnowledgeEntryRow], *, header: str) -> str:
    if not entries:
        return f"{header}\n(нет записей)"
    lines = [header]
    for e in entries:
        cat = e.category
        lines.append(f"### {e.title} [{cat}]\n{e.content.strip()}")
    return "\n\n".join(lines)


async def sync_profile_to_knowledge(
    conn: asyncpg.Connection,
    user_id: UUID,
    profile: dict[str, Any],
) -> None:
    mapping = {
        "name": ("profile", "Имя"),
        "sphere": ("profile", "Сфера / занятость"),
        "tools": ("profile", "Инструменты"),
        "goals": ("profile", "Цели автоматизации"),
        "notes": ("preferences", "Заметки"),
    }
    for key, (cat, title) in mapping.items():
        val = profile.get(key)
        if val is None or str(val).strip() == "":
            continue
        await upsert_user_fact(
            conn,
            user_id,
            category=cat,
            title=title,
            content=str(val).strip(),
            tags=["onboarding", key],
        )
    summary = profile.get("summary")
    if summary:
        await upsert_user_fact(
            conn,
            user_id,
            category="profile",
            title="Сводка онбординга",
            content=str(summary).strip(),
            tags=["onboarding", "summary"],
        )
