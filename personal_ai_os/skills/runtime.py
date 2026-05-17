"""Anthropic tool schemas and dispatch for skill-backed agents."""

from __future__ import annotations

from typing import Any
from uuid import UUID

import asyncpg
from redis.asyncio import Redis

from personal_ai_os.config import Settings, get_settings
from personal_ai_os.core.claude_client import ClaudeClient
from personal_ai_os.db.models import AgentRow
from personal_ai_os.skills.catalog import SKILL_CATALOG, skill_ids_for_agent
from personal_ai_os.skills.executors import execute_skill_tool

TOOL_SCHEMAS: dict[str, dict[str, Any]] = {
    "web_search": {
        "name": "web_search",
        "description": "Поиск в интернете (Tavily)",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "max_results": {"type": "integer"},
            },
            "required": ["query"],
        },
    },
    "get_weather": {
        "name": "get_weather",
        "description": "Прогноз погоды по городу",
        "input_schema": {
            "type": "object",
            "properties": {"city": {"type": "string"}},
            "required": ["city"],
        },
    },
    "summarize_url": {
        "name": "summarize_url",
        "description": "Краткое содержание страницы по URL",
        "input_schema": {
            "type": "object",
            "properties": {"url": {"type": "string"}},
            "required": ["url"],
        },
    },
    "fetch_url_text": {
        "name": "fetch_url_text",
        "description": "Скачать текст страницы (без саммари)",
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string"},
                "max_chars": {"type": "integer"},
            },
            "required": ["url"],
        },
    },
    "humanize_text": {
        "name": "humanize_text",
        "description": "Переписать текст естественнее",
        "input_schema": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
    },
    "schedule_reminder": {
        "name": "schedule_reminder",
        "description": "Сохранить напоминание (when: дата/время, message: текст)",
        "input_schema": {
            "type": "object",
            "properties": {
                "when": {"type": "string"},
                "message": {"type": "string"},
            },
            "required": ["when", "message"],
        },
    },
}


def tool_names_for_skill_ids(skill_ids: list[str]) -> list[str]:
    names: list[str] = []
    for sid in skill_ids:
        tpl = SKILL_CATALOG.get(sid)
        if not tpl:
            continue
        for n in tpl.tool_names:
            if n not in names:
                names.append(n)
    return names


def build_anthropic_tools(skill_ids: list[str]) -> list[dict[str, Any]]:
    names = tool_names_for_skill_ids(skill_ids)
    return [TOOL_SCHEMAS[n] for n in names if n in TOOL_SCHEMAS]


def agent_has_skill_tools(agent: AgentRow) -> bool:
    meta = agent.metadata if isinstance(agent.metadata, dict) else {}
    ids = skill_ids_for_agent(list(agent.tools or []), meta)
    return bool(tool_names_for_skill_ids(ids))


def get_agent_skill_ids(agent: AgentRow) -> list[str]:
    meta = agent.metadata if isinstance(agent.metadata, dict) else {}
    return skill_ids_for_agent(list(agent.tools or []), meta)


def make_tool_executor(
    *,
    settings: Settings,
    claude: ClaudeClient,
    conn: asyncpg.Connection | None,
    user_id: UUID | None,
    redis: Redis | None,
):
    async def exec_tool(name: str, payload: dict[str, Any]) -> str:
        return await execute_skill_tool(
            name,
            payload,
            settings=settings,
            claude=claude,
            conn=conn,
            user_id=user_id,
            redis=redis,
        )

    return exec_tool
