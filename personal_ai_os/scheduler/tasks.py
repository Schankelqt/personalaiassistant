from __future__ import annotations

import asyncio

from celery import Celery
from celery.schedules import crontab

from personal_ai_os.config import get_settings

settings = get_settings()
celery_app = Celery(
    "personal_ai_os",
    broker=settings.redis_url,
    backend=settings.redis_url,
)

celery_app.conf.timezone = "UTC"
celery_app.conf.beat_schedule = {
    "reset-daily-tokens": {
        "task": "personal_ai_os.scheduler.tasks.reset_daily_tokens",
        "schedule": crontab(hour=0, minute=5),
    },
    "birthday-reminders": {
        "task": "personal_ai_os.scheduler.tasks.send_birthday_reminders",
        "schedule": crontab(minute=0),
    },
    "apply-due-plan-changes": {
        "task": "personal_ai_os.scheduler.tasks.apply_due_plan_changes",
        "schedule": crontab(minute="*/15"),
    },
    "meeting-reminders": {
        "task": "personal_ai_os.scheduler.tasks.send_meeting_reminders",
        "schedule": crontab(minute="*/5"),
    },
}


@celery_app.task(name="personal_ai_os.scheduler.tasks.send_birthday_reminders")
def send_birthday_reminders() -> str:
    async def run() -> None:
        from datetime import date, datetime
        from zoneinfo import ZoneInfo

        import httpx

        from personal_ai_os.config import get_settings
        from personal_ai_os.db.pool import get_pool
        from personal_ai_os.db import queries

        settings = get_settings()
        pool = await get_pool()
        def days_until(today: date, bday: date) -> int:
            nxt = bday.replace(year=today.year)
            if nxt < today:
                nxt = bday.replace(year=today.year + 1)
            return (nxt - today).days

        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT id, telegram_id, timezone, reminder_hour FROM users WHERE onboarding_complete = true"
            )
            for u in rows:
                tz_name = u.get("timezone") or "UTC"
                target_hour = int(u.get("reminder_hour") or 9)
                try:
                    user_now = datetime.now(ZoneInfo(tz_name))
                except Exception:
                    user_now = datetime.now(ZoneInfo("UTC"))
                # Требование BRD: напоминания по умолчанию в 09:00 локального времени пользователя.
                if user_now.hour != target_hour:
                    continue
                today = user_now.date()
                people = await queries.list_people(conn, u["id"])
                for p in people:
                    if not p.birthday:
                        continue
                    d = days_until(today, p.birthday)
                    if d not in (7, 1):
                        continue
                    text = (
                        f"Напоминание: через {d} дн. день рождения у {p.name or 'контакта'} "
                        f"({p.birthday.strftime('%d.%m')})."
                    )
                    async with httpx.AsyncClient(timeout=30) as c:
                        await c.post(
                            f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage",
                            json={
                                "chat_id": u["telegram_id"],
                                "text": text,
                            },
                        )

    asyncio.run(run())
    return "ok"


@celery_app.task(name="personal_ai_os.scheduler.tasks.reset_daily_tokens")
def reset_daily_tokens() -> str:
    async def run() -> None:
        from personal_ai_os.db.pool import get_pool
        from personal_ai_os.db import queries

        pool = await get_pool()
        async with pool.acquire() as conn:
            await queries.reset_daily_tokens_all(conn)

    asyncio.run(run())
    return "ok"


@celery_app.task(name="personal_ai_os.scheduler.tasks.apply_due_plan_changes")
def apply_due_plan_changes() -> str:
    async def run() -> int:
        from personal_ai_os.db.pool import get_pool
        from personal_ai_os.db import queries

        pool = await get_pool()
        async with pool.acquire() as conn:
            return await queries.apply_due_plan_changes(conn)

    changed = asyncio.run(run())
    return f"changed={changed}"


@celery_app.task(name="personal_ai_os.scheduler.tasks.send_meeting_reminders")
def send_meeting_reminders() -> str:
    async def run() -> int:
        from datetime import datetime, timedelta, timezone
        from zoneinfo import ZoneInfo

        import httpx
        import redis.asyncio as aioredis

        from personal_ai_os.db.pool import get_pool
        from personal_ai_os.db import queries
        from personal_ai_os.services.work_service import get_google_access_token

        pool = await get_pool()
        sent = 0
        redis = aioredis.from_url(settings.redis_url, decode_responses=True)
        try:
            async with pool.acquire() as conn:
                users = await conn.fetch(
                    "SELECT id, telegram_id, timezone, meeting_reminder_minutes FROM users WHERE onboarding_complete = true"
                )
                for u in users:
                    user = await queries.get_user_by_id(conn, u["id"])
                    if not user:
                        continue
                    try:
                        token = await get_google_access_token(conn, user)
                    except Exception:
                        continue
                    tz_name = u.get("timezone") or "UTC"
                    try:
                        now_local = datetime.now(ZoneInfo(tz_name))
                    except Exception:
                        now_local = datetime.now(ZoneInfo("UTC"))
                    lead = int(u.get("meeting_reminder_minutes") or 15)
                    tmin_local = now_local + timedelta(minutes=lead - 2)
                    tmax_local = now_local + timedelta(minutes=lead + 2)
                    tmin = tmin_local.astimezone(timezone.utc).isoformat()
                    tmax = tmax_local.astimezone(timezone.utc).isoformat()
                    async with httpx.AsyncClient(timeout=30) as c:
                        r = await c.get(
                            "https://www.googleapis.com/calendar/v3/calendars/primary/events",
                            headers={"Authorization": f"Bearer {token}"},
                            params={
                                "timeMin": tmin,
                                "timeMax": tmax,
                                "singleEvents": True,
                                "orderBy": "startTime",
                                "maxResults": 10,
                            },
                        )
                        if r.status_code >= 400:
                            continue
                        items = r.json().get("items", [])
                    for it in items:
                        eid = it.get("id")
                        st = it.get("start", {}).get("dateTime")
                        if not eid or not st:
                            continue
                        dedupe = f"meet-rem:{u['id']}:{eid}:{st}"
                        ok = await redis.set(dedupe, "1", ex=86400, nx=True)
                        if not ok:
                            continue
                        txt = (
                            f"Напоминание: встреча через ~{lead} мин\n"
                            f"{it.get('summary', 'Без названия')}\n"
                            f"Старт: {st}"
                        )
                        async with httpx.AsyncClient(timeout=30) as c:
                            await c.post(
                                f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage",
                                json={"chat_id": u["telegram_id"], "text": txt},
                            )
                        sent += 1
        finally:
            await redis.aclose()
        return sent

    count = asyncio.run(run())
    return f"sent={count}"
