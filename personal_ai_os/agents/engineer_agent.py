from __future__ import annotations

import uuid
from typing import Any
from urllib.parse import urlencode

import asyncpg
from redis.asyncio import Redis

from personal_ai_os.config import get_settings
from personal_ai_os.core.claude_client import ClaudeClient
from personal_ai_os.core.excel_export import build_xlsx_from_spec
from personal_ai_os.core.message_attachments import attachments_append
from personal_ai_os.core.session_store import append_message
from personal_ai_os.db import queries
from personal_ai_os.db.models import AgentRow, UserRow


async def run_engineer(
    conn: asyncpg.Connection,
    redis: Redis,
    claude: ClaudeClient,
    user: UserRow,
    agent: AgentRow,
    query: str,
) -> str:
    settings = get_settings()
    system = f"""Ты — Engineer-агент: настройка ассистента, интеграции, создание агентов, таблицы Excel.
Отвечай по-русски, кратко. Используй инструменты.
Для .xlsx вызывай create_excel_workbook с file_name и sheets (headers опционально, rows — массив строк-ячеек).
{agent.system_prompt}
"""

    async def oauth_google_link() -> str:
        if not settings.google_client_id:
            return "Google OAuth не настроен администратором."
        import secrets

        state = secrets.token_hex(16)
        await redis.setex(f"oauth_state:{state}", 600, str(user.id))
        params = {
            "client_id": settings.google_client_id,
            "redirect_uri": settings.google_redirect_uri,
            "response_type": "code",
            "scope": "https://www.googleapis.com/auth/calendar",
            "access_type": "offline",
            "prompt": "consent",
            "state": state,
        }
        url = "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(params)
        return f"Ссылка для авторизации Google Calendar:\n{url}"

    async def oauth_jira_link() -> str:
        if not settings.jira_client_id:
            return "Jira OAuth не настроен администратором."
        import secrets

        state = secrets.token_hex(16)
        await redis.setex(f"oauth_state:{state}", 600, str(user.id))
        params = {
            "audience": "api.atlassian.com",
            "client_id": settings.jira_client_id,
            "scope": "read:jira-user read:jira-work write:jira-work offline_access",
            "redirect_uri": settings.jira_redirect_uri,
            "response_type": "code",
            "prompt": "consent",
            "state": state,
        }
        url = "https://auth.atlassian.com/authorize?" + urlencode(params)
        return f"Ссылка для авторизации Jira:\n{url}"

    tools = [
        {
            "name": "create_custom_agent",
            "description": "Создать пользовательского агента",
            "input_schema": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "instructions": {"type": "string"},
                },
                "required": ["name", "instructions"],
            },
        },
        {
            "name": "toggle_agent",
            "description": "Включить/выключить агента по id",
            "input_schema": {
                "type": "object",
                "properties": {
                    "agent_id": {"type": "string"},
                    "active": {"type": "boolean"},
                },
                "required": ["agent_id", "active"],
            },
        },
        {
            "name": "update_custom_agent",
            "description": "Обновить агента: имя и/или инструкции",
            "input_schema": {
                "type": "object",
                "properties": {
                    "agent_id": {"type": "string"},
                    "name": {"type": "string"},
                    "instructions": {"type": "string"},
                },
                "required": ["agent_id"],
            },
        },
        {
            "name": "get_oauth_google_link",
            "description": "Выдать ссылку OAuth Google Calendar",
            "input_schema": {"type": "object", "properties": {}},
        },
        {
            "name": "get_oauth_jira_link",
            "description": "Выдать ссылку OAuth Jira Cloud",
            "input_schema": {"type": "object", "properties": {}},
        },
        {
            "name": "create_excel_workbook",
            "description": (
                "Собрать файл Excel (.xlsx) и отправить пользователю в Telegram. "
                "До 10 листов, до 2000 строк данных на файл."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "file_name": {
                        "type": "string",
                        "description": "Имя файла, например отчет_март.xlsx",
                    },
                    "sheets": {
                        "type": "array",
                        "description": "Листы с данными",
                        "items": {
                            "type": "object",
                            "properties": {
                                "sheet_name": {"type": "string"},
                                "headers": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                },
                                "rows": {
                                    "type": "array",
                                    "items": {"type": "array"},
                                },
                            },
                            "required": ["rows"],
                        },
                    },
                },
                "required": ["file_name", "sheets"],
            },
        },
    ]

    async def exec_tool(name: str, payload: dict[str, Any]) -> str:
        if name == "create_custom_agent":
            max_a = queries.max_agents_for_plan(user.plan)
            cnt = await queries.count_active_agents(conn, user.id)
            if cnt >= max_a:
                return f"Лимит агентов для тарифа ({max_a}). Обновите план: /upgrade"
            nid = await queries.insert_agent(
                conn,
                user.id,
                name=payload.get("name") or "Агент",
                agent_type="custom",
                system_prompt=payload.get("instructions") or "",
                tools=[],
                metadata={},
            )
            return f"Создан агент {nid.name} ({nid.id})"
        if name == "toggle_agent":
            try:
                aid = uuid.UUID(payload.get("agent_id", ""))
            except ValueError:
                return "Неверный id"
            await queries.update_agent(
                conn,
                user.id,
                aid,
                is_active=bool(payload.get("active")),
            )
            return "Обновлено"
        if name == "update_custom_agent":
            try:
                aid = uuid.UUID(payload.get("agent_id", ""))
            except ValueError:
                return "Неверный id"
            ok = await queries.update_agent(
                conn,
                user.id,
                aid,
                name=payload.get("name"),
                system_prompt=payload.get("instructions"),
            )
            return "Агент обновлён" if ok else "Агент не найден"
        if name == "get_oauth_google_link":
            return await oauth_google_link()
        if name == "get_oauth_jira_link":
            return await oauth_jira_link()
        if name == "create_excel_workbook":
            try:
                raw, fname = build_xlsx_from_spec(
                    {"file_name": payload.get("file_name"), "sheets": payload.get("sheets")}
                )
            except ValueError as e:
                return f"Не удалось собрать Excel: {e}"
            attachments_append(fname, raw)
            return f"Файл «{fname}» сформирован и будет отправлен вместе с ответом."
        return "?"

    res = await claude.complete_with_tools(
        model=claude.pick_model(use_haiku=False),
        system=system,
        messages=[{"role": "user", "content": query}],
        tools=tools,
        tool_executor=exec_tool,
    )
    u = await queries.get_user_by_id(conn, user.id)
    if u:
        await queries.finalize_llm_usage(
            conn, u, agent.id, res.model, res.input_tokens, res.output_tokens
        )
    await append_message(redis, user.id, "user", query)
    await append_message(redis, user.id, "assistant", res.text)
    return res.text
