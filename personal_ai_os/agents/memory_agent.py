from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from uuid import UUID

import asyncpg
from dateutil import parser as date_parser
from redis.asyncio import Redis

from personal_ai_os.core.claude_client import ClaudeClient
from personal_ai_os.core.prompt_builder import build_agent_system_prompt
from personal_ai_os.core.session_store import append_message, get_messages
from personal_ai_os.db import queries
from personal_ai_os.db.models import AgentRow, UserRow


def _parse_date(s: str | None) -> Any:
    if not s:
        return None
    try:
        return date_parser.parse(s, dayfirst=True).date()
    except (ValueError, TypeError):
        return None


async def run_memory(
    conn: asyncpg.Connection,
    redis: Redis,
    claude: ClaudeClient,
    user: UserRow,
    agent: AgentRow,
    query: str,
    *,
    thread_id: int | None = None,
) -> str:
    system = build_agent_system_prompt(
        agent,
        user,
        preamble="Ты — Memory-агент: персональная память (люди, дни рождения, заметки).",
    )

    tools = [
        {
            "name": "save_person",
            "description": "Сохранить человека: имя, birthday ISO (YYYY-MM-DD), отношение, tg username, заметки",
            "input_schema": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "birthday": {"type": "string"},
                    "relation": {"type": "string"},
                    "tg_username": {"type": "string"},
                    "notes": {"type": "string"},
                },
                "required": ["name"],
            },
        },
        {
            "name": "delete_person",
            "description": "Удалить человека по UUID из памяти",
            "input_schema": {
                "type": "object",
                "properties": {"entry_id": {"type": "string"}},
                "required": ["entry_id"],
            },
        },
        {
            "name": "delete_person_by_name",
            "description": "Удалить человека по точному имени",
            "input_schema": {
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
            },
        },
        {
            "name": "list_people_snapshot",
            "description": "Получить текущий список людей (кратко)",
            "input_schema": {"type": "object", "properties": {}},
        },
        {
            "name": "save_note",
            "description": "Произвольная заметка с тегами",
            "input_schema": {
                "type": "object",
                "properties": {
                    "content": {"type": "string"},
                    "tags": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["content"],
            },
        },
        {
            "name": "generate_birthday_text",
            "description": "Сгенерировать поздравление для person_id",
            "input_schema": {
                "type": "object",
                "properties": {"entry_id": {"type": "string"}},
                "required": ["entry_id"],
            },
        },
    ]

    async def exec_tool(name: str, payload: dict[str, Any]) -> str:
        if name == "save_person":
            pid = await queries.insert_memory_person(
                conn,
                user.id,
                name=payload.get("name") or "?",
                birthday=_parse_date(payload.get("birthday")),
                relation=payload.get("relation"),
                tg_username=payload.get("tg_username"),
                notes=payload.get("notes"),
            )
            return f"Сохранено, id={pid}"
        if name == "delete_person":
            try:
                eid = UUID(payload.get("entry_id", ""))
            except ValueError:
                return "Неверный id"
            ok = await queries.delete_memory_entry(conn, user.id, eid)
            return "Удалено" if ok else "Не найдено"
        if name == "delete_person_by_name":
            target = (payload.get("name") or "").strip()
            if not target:
                return "Укажите имя"
            n = await queries.delete_person_by_name(conn, user.id, target)
            return f"Удалено записей: {n}" if n else "Не найдено"
        if name == "list_people_snapshot":
            people = await queries.list_people(conn, user.id)
            lines = [f"{p.name} ({p.id}) ДР={p.birthday}" for p in people]
            return "\n".join(lines) or "Пусто"
        if name == "save_note":
            tags = payload.get("tags") or []
            nid = await queries.insert_note(
                conn,
                user.id,
                payload.get("content") or "",
                list(tags) if isinstance(tags, list) else [],
            )
            return f"Заметка сохранена id={nid}"
        if name == "generate_birthday_text":
            try:
                eid = UUID(payload.get("entry_id", ""))
            except ValueError:
                return "Неверный id"
            p = await queries.get_memory_entry(conn, user.id, eid)
            if not p or not p.name:
                return "Не найдено"
            prompt = f"""Напиши поздравление с ДР для {p.name}.
Отношение: {p.relation or '—'}.
Заметки: {p.notes or '—'}.
2–4 предложения, тепло, не шаблонно, по-русски."""
            r = await claude.complete(
                model=claude.pick_model(use_haiku=False),
                system="Ты помогаешь писать поздравления.",
                messages=[{"role": "user", "content": prompt}],
            )
            u = await queries.get_user_by_id(conn, user.id)
            if u:
                await queries.finalize_llm_usage(
                    conn, u, agent.id, r.model, r.input_tokens, r.output_tokens
                )
            return r.text
        return "ok"

    prior = await get_messages(redis, user.id, limit=20, thread_id=thread_id)
    messages = prior + [{"role": "user", "content": query}]

    res = await claude.complete_with_tools(
        model=claude.pick_model(use_haiku=True),
        system=system,
        messages=messages,
        tools=tools,
        tool_executor=exec_tool,
    )
    u = await queries.get_user_by_id(conn, user.id)
    if u:
        await queries.finalize_llm_usage(
            conn, u, agent.id, res.model, res.input_tokens, res.output_tokens
        )
    await append_message(redis, user.id, "user", query, thread_id=thread_id)
    await append_message(redis, user.id, "assistant", res.text, thread_id=thread_id)
    return res.text
