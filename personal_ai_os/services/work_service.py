from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
from typing import Any

import asyncpg
import httpx

from personal_ai_os.config import get_settings
from personal_ai_os.core.claude_client import ClaudeClient
from personal_ai_os.core.crypto import decrypt, encrypt
from personal_ai_os.db import queries
from personal_ai_os.db.models import AgentRow, UserRow


async def _refresh_google(refresh_token: str) -> dict[str, Any]:
    s = get_settings()
    async with httpx.AsyncClient(timeout=8) as c:
        r = await c.post(
            "https://oauth2.googleapis.com/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": s.google_client_id,
                "client_secret": s.google_client_secret,
            },
        )
        r.raise_for_status()
        return r.json()


async def _refresh_jira(refresh_token: str) -> dict[str, Any]:
    s = get_settings()
    async with httpx.AsyncClient(timeout=8) as c:
        r = await c.post(
            "https://auth.atlassian.com/oauth/token",
            json={
                "grant_type": "refresh_token",
                "client_id": s.jira_client_id,
                "client_secret": s.jira_client_secret,
                "refresh_token": refresh_token,
            },
        )
        r.raise_for_status()
        return r.json()


async def get_google_access_token(conn: asyncpg.Connection, user: UserRow) -> str:
    row = await queries.get_oauth(conn, user.id, "google")
    if not row:
        raise RuntimeError("Google не подключён. Скажите: «подключи Google Calendar».")
    key = get_settings().oauth_encryption_key_hex
    refresh = decrypt(row.refresh_token_enc, key)
    access = decrypt(row.access_token_enc, key)
    exp = row.expires_at
    if exp and exp - timedelta(minutes=2) > datetime.now(timezone.utc):
        return access
    data = await _refresh_google(refresh)
    new_access = data["access_token"]
    new_refresh = data.get("refresh_token", refresh)
    expires_in = data.get("expires_in", 3600)
    new_exp = datetime.now(timezone.utc) + timedelta(seconds=int(expires_in))
    await queries.upsert_oauth(
        conn,
        user.id,
        "google",
        encrypt(new_access, key),
        encrypt(new_refresh, key),
        new_exp,
        row.scope,
        None,
        None,
    )
    return new_access


async def get_jira_cloud_context(conn: asyncpg.Connection, user: UserRow) -> tuple[str, str]:
    row = await queries.get_oauth(conn, user.id, "jira")
    if not row or not row.jira_cloud_id:
        raise RuntimeError("Jira не подключена. Скажите: «подключи Jira».")
    key = get_settings().oauth_encryption_key_hex
    refresh = decrypt(row.refresh_token_enc, key)
    access = decrypt(row.access_token_enc, key)
    exp = row.expires_at
    if exp and exp - timedelta(minutes=2) > datetime.now(timezone.utc):
        return access, row.jira_cloud_id
    data = await _refresh_jira(refresh)
    new_access = data["access_token"]
    new_refresh = data.get("refresh_token", refresh)
    expires_in = data.get("expires_in", 3600)
    new_exp = datetime.now(timezone.utc) + timedelta(seconds=int(expires_in))
    await queries.upsert_oauth(
        conn,
        user.id,
        "jira",
        encrypt(new_access, key),
        encrypt(new_refresh, key),
        new_exp,
        row.scope,
        row.jira_cloud_id,
        row.jira_base_url,
    )
    return new_access, row.jira_cloud_id


async def run_work_agent(
    conn: asyncpg.Connection,
    claude: ClaudeClient,
    user: UserRow,
    query: str,
) -> str:
    """Work-агент: Claude + инструменты Jira/GCal."""
    work = await conn.fetchrow(
        "SELECT * FROM agents WHERE user_id = $1 AND agent_type = 'work' AND is_active LIMIT 1",
        user.id,
    )
    if not work:
        return "Work-агент не найден. Пройдите онбординг или создайте его через Engineer."
    agent = AgentRow.model_validate(dict(work))

    async def exec_tool(name: str, payload: dict[str, Any]) -> str:
        try:
            if name == "gcal_list_events":
                token = await get_google_access_token(conn, user)
                tmin = payload.get("time_min")
                tmax = payload.get("time_max")
                async with httpx.AsyncClient(timeout=8) as c:
                    r = await c.get(
                        "https://www.googleapis.com/calendar/v3/calendars/primary/events",
                        headers={"Authorization": f"Bearer {token}"},
                        params={
                            "timeMin": tmin,
                            "timeMax": tmax,
                            "singleEvents": True,
                            "orderBy": "startTime",
                            "maxResults": 20,
                        },
                    )
                    r.raise_for_status()
                    data = r.json()
                items = data.get("items", [])
                lines = []
                for it in items:
                    st = it.get("start", {}).get("dateTime") or it.get("start", {}).get("date")
                    lines.append(f"- {it.get('summary', 'без названия')} | {st}")
                return "\n".join(lines) or "Событий нет."
            if name == "gcal_create_event":
                token = await get_google_access_token(conn, user)
                dedupe = payload.get("idempotency_key") or hashlib.sha256(
                    f"{user.id}:gcal:{payload.get('title')}:{payload.get('start')}:{payload.get('end')}".encode()
                ).hexdigest()
                body = {
                    "summary": payload.get("title", "Событие"),
                    "start": {"dateTime": payload.get("start"), "timeZone": payload.get("tz", "UTC")},
                    "end": {"dateTime": payload.get("end"), "timeZone": payload.get("tz", "UTC")},
                }
                async with httpx.AsyncClient(timeout=8) as c:
                    r = await c.post(
                        "https://www.googleapis.com/calendar/v3/calendars/primary/events",
                        headers={
                            "Authorization": f"Bearer {token}",
                            "Content-Type": "application/json",
                            "X-Idempotency-Key": dedupe,
                        },
                        json=body,
                    )
                    r.raise_for_status()
                    out = r.json()
                return f"Создано: {out.get('htmlLink', 'ok')}"
            if name == "jira_search_my_issues":
                token, cloud = await get_jira_cloud_context(conn, user)
                jql = payload.get("jql") or "assignee = currentUser() ORDER BY updated DESC"
                async with httpx.AsyncClient(timeout=8) as c:
                    r = await c.get(
                        f"https://api.atlassian.com/ex/jira/{cloud}/rest/api/3/search",
                        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
                        params={"jql": jql, "maxResults": 15},
                    )
                    r.raise_for_status()
                    data = r.json()
                issues = data.get("issues", [])
                lines = [f"- {i['key']}: {i['fields'].get('summary', '')}" for i in issues]
                return "\n".join(lines) or "Задач нет."
            if name == "jira_create_issue":
                token, cloud = await get_jira_cloud_context(conn, user)
                dedupe = payload.get("idempotency_key") or hashlib.sha256(
                    f"{user.id}:jira:create:{payload.get('project_key')}:{payload.get('summary')}".encode()
                ).hexdigest()
                project_key = payload.get("project_key")
                summary = payload.get("summary", "Задача")
                description = payload.get("description", "")
                body = {
                    "fields": {
                        "project": {"key": project_key},
                        "summary": summary,
                        "description": {
                            "type": "doc",
                            "version": 1,
                            "content": [
                                {
                                    "type": "paragraph",
                                    "content": [{"type": "text", "text": description}],
                                }
                            ],
                        },
                        "issuetype": {"name": payload.get("issue_type", "Task")},
                    }
                }
                async with httpx.AsyncClient(timeout=8) as c:
                    r = await c.post(
                        f"https://api.atlassian.com/ex/jira/{cloud}/rest/api/3/issue",
                        headers={
                            "Authorization": f"Bearer {token}",
                            "Content-Type": "application/json",
                            "X-Idempotency-Key": dedupe,
                        },
                        json=body,
                    )
                    r.raise_for_status()
                    out = r.json()
                return f"Создано: {out.get('key')} {out.get('self', '')}"
            if name == "jira_transition":
                token, cloud = await get_jira_cloud_context(conn, user)
                dedupe = payload.get("idempotency_key") or hashlib.sha256(
                    f"{user.id}:jira:transition:{payload.get('issue_key')}:{payload.get('transition_id')}".encode()
                ).hexdigest()
                key = payload.get("issue_key")
                tid = payload.get("transition_id")
                async with httpx.AsyncClient(timeout=8) as c:
                    r = await c.post(
                        f"https://api.atlassian.com/ex/jira/{cloud}/rest/api/3/issue/{key}/transitions",
                        headers={
                            "Authorization": f"Bearer {token}",
                            "Content-Type": "application/json",
                            "X-Idempotency-Key": dedupe,
                        },
                        json={"transition": {"id": tid}},
                    )
                    r.raise_for_status()
                return "Статус обновлён"
        except RuntimeError as e:
            msg = str(e)
            if "Google не подключён" in msg:
                return "Google Calendar не подключен. Напишите: «подключи Google Calendar», я пришлю ссылку OAuth."
            if "Jira не подключена" in msg:
                return "Jira не подключена. Напишите: «подключи Jira», я пришлю ссылку OAuth."
            return msg
        except httpx.HTTPStatusError as e:
            if e.response.status_code in (401, 403):
                return (
                    "Доступ к интеграции истек или отозван. "
                    "Переавторизуйтесь: «подключи Google Calendar» или «подключи Jira»."
                )
            return f"Ошибка интеграции: HTTP {e.response.status_code}"
        except httpx.TimeoutException:
            return (
                "Интеграция ответила слишком долго (более 8 сек). "
                "Попробуйте повторить запрос чуть позже."
            )
        except Exception as e:
            return f"Ошибка интеграции: {e!s}"
        return "?"

    tools = [
        {
            "name": "gcal_list_events",
            "description": "Список событий Google Calendar (time_min/max в RFC3339)",
            "input_schema": {
                "type": "object",
                "properties": {
                    "time_min": {"type": "string"},
                    "time_max": {"type": "string"},
                },
                "required": ["time_min", "time_max"],
            },
        },
        {
            "name": "gcal_create_event",
            "description": "Создать событие: title, start, end (RFC3339), tz",
            "input_schema": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "start": {"type": "string"},
                    "end": {"type": "string"},
                    "tz": {"type": "string"},
                },
                "required": ["title", "start", "end"],
            },
        },
        {
            "name": "jira_search_my_issues",
            "description": "Поиск задач JQL",
            "input_schema": {
                "type": "object",
                "properties": {"jql": {"type": "string"}},
            },
        },
        {
            "name": "jira_create_issue",
            "description": "Создать задачу: project_key, summary, description, issue_type",
            "input_schema": {
                "type": "object",
                "properties": {
                    "project_key": {"type": "string"},
                    "summary": {"type": "string"},
                    "description": {"type": "string"},
                    "issue_type": {"type": "string"},
                },
                "required": ["project_key", "summary"],
            },
        },
        {
            "name": "jira_transition",
            "description": "Перевести задачу: issue_key, transition_id",
            "input_schema": {
                "type": "object",
                "properties": {
                    "issue_key": {"type": "string"},
                    "transition_id": {"type": "string"},
                },
                "required": ["issue_key", "transition_id"],
            },
        },
    ]

    system = f"""Ты — Work-агент: Jira и Google Calendar через инструменты.
Спроси project_key для Jira если неизвестен. Время в календаре — RFC3339.
Пользователь: {user.full_name}.
{agent.system_prompt}
"""
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
    return res.text
