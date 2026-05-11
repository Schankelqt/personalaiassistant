from __future__ import annotations

import json
from typing import Any

from redis.asyncio import Redis

from personal_ai_os.db import queries
from personal_ai_os.db.models import UserRow

KEY = "onboarding:{}"


def _default_engineer_prompt(profile: dict[str, Any]) -> str:
    return (
        f"Профиль: {profile.get('sphere', '—')}. Инструменты: {profile.get('tools', '—')}. "
        f"Цели: {profile.get('goals', '—')}. Помогаешь настраивать агентов и интеграции."
    )


def _default_memory_prompt(_profile: dict[str, Any]) -> str:
    return "Помнишь людей, дни рождения, заметки о близких."


def _default_work_prompt(profile: dict[str, Any]) -> str:
    return (
        f"Рабочий ассистент для Jira и Google Calendar. Контекст: {profile.get('sphere', '')} "
        f"{profile.get('tools', '')}"
    )


async def begin_onboarding(redis: Redis, user_id: Any) -> None:
    await redis.setex(
        KEY.format(user_id),
        7200,
        json.dumps({"step": 1, "data": {}}, ensure_ascii=False),
    )


async def clear_onboarding(redis: Redis, user_id: Any) -> None:
    await redis.delete(KEY.format(user_id))


async def get_state(redis: Redis, user_id: Any) -> dict[str, Any] | None:
    raw = await redis.get(KEY.format(user_id))
    if not raw:
        return None
    return json.loads(raw)


async def save_state(redis: Redis, user_id: Any, state: dict[str, Any]) -> None:
    await redis.setex(KEY.format(user_id), 7200, json.dumps(state, ensure_ascii=False))


QUESTIONS = [
    "Привет! Я Engineer — настрою твоего ассистента. Как тебя зовут? (можно имя или ник)",
    "Чем ты занимаешься? (PM, разработка, фриланс, предприниматель — как удобно)",
    "Какими рабочими инструментами пользуешься? (Jira, Notion, Google Calendar...)",
    "Что хочешь автоматизировать в первую очередь?",
    "Хочешь запомнить дни рождения близких? Напиши через запятую: Имя — ДД.ММ или «пропустить».",
]


async def finalize_onboarding(conn: Any, user_id: Any, profile: dict[str, Any]) -> None:
    await queries.update_user_profile(conn, user_id, onboarding_complete=True)
    existing = await queries.count_active_agents(conn, user_id)
    if existing > 0:
        return
    await queries.insert_agent(
        conn,
        user_id,
        name="Engineer",
        agent_type="engineer",
        system_prompt=_default_engineer_prompt(profile),
        tools=["create_custom_agent", "oauth", "excel"],
        metadata={"role": "engineer"},
    )
    await queries.insert_agent(
        conn,
        user_id,
        name="Память",
        agent_type="memory",
        system_prompt=_default_memory_prompt(profile),
        tools=["memory"],
        metadata={"role": "memory"},
    )
    tl = (profile.get("tools") or "").lower()
    sp = (profile.get("sphere") or "").lower()
    need_work = any(x in tl for x in ("jira", "календар", "calendar", "google")) or any(
        x in sp for x in ("pm", "продакт", "manager", "менедж")
    )
    if need_work:
        await queries.insert_agent(
            conn,
            user_id,
            name="Работа",
            agent_type="work",
            system_prompt=_default_work_prompt(profile),
            tools=["jira", "gcal"],
            metadata={"role": "work"},
        )


async def handle_onboarding_message(
    conn: Any,
    redis: Redis,
    user: UserRow,
    text: str,
) -> tuple[str, bool]:
    """Returns (reply, completed)."""
    state = await get_state(redis, user.id)
    if state is None:
        await begin_onboarding(redis, user.id)
        state = {"step": 1, "data": {}}

    step = int(state["step"])
    data = dict(state["data"])

    if step == 1:
        data["name"] = text.strip()
        await queries.update_user_profile(conn, user.id, full_name=data["name"])
        await save_state(redis, user.id, {"step": 2, "data": data})
        return QUESTIONS[1], False

    if step == 2:
        data["sphere"] = text.strip()
        await save_state(redis, user.id, {"step": 3, "data": data})
        return QUESTIONS[2], False

    if step == 3:
        data["tools"] = text.strip()
        await save_state(redis, user.id, {"step": 4, "data": data})
        return QUESTIONS[3], False

    if step == 4:
        data["goals"] = text.strip()
        await save_state(redis, user.id, {"step": 5, "data": data})
        return QUESTIONS[4], False

    if step == 5:
        low = text.strip().lower()
        if low not in ("пропустить", "skip", "нет"):
            parts = [p.strip() for p in text.split(",") if p.strip()]
            from dateutil import parser as dp

            for chunk in parts:
                if "—" in chunk:
                    name, _, rest = chunk.partition("—")
                elif "-" in chunk:
                    name, _, rest = chunk.partition("-")
                else:
                    name, rest = chunk, ""
                name = name.strip()
                rest = rest.strip()
                if not name:
                    continue
                bday = None
                if rest:
                    try:
                        bday = dp.parse(rest, dayfirst=True).date()
                    except (ValueError, TypeError):
                        pass
                await queries.insert_memory_person(
                    conn,
                    user.id,
                    name=name,
                    birthday=bday,
                    relation="other",
                    tg_username=None,
                    notes=None,
                )
        await finalize_onboarding(conn, user.id, data)
        await clear_onboarding(redis, user.id)
        agents = await queries.list_agents(conn, user.id)
        agent_names = ", ".join(a.name for a in agents)
        return (
            "Онбординг завершён. Созданы агенты: "
            f"{agent_names}.\n\n"
            "Подключи интеграции: «подключи Google Calendar» или «подключи Jira». "
            "Команды: /agents, /status, /people.",
            True,
        )

    await clear_onboarding(redis, user.id)
    return "Используй /setup чтобы пройти настройку снова.", False
