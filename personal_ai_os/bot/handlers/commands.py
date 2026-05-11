from __future__ import annotations

from typing import TYPE_CHECKING

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

if TYPE_CHECKING:
    from personal_ai_os.bot.setup import BotContext

WELCOME = (
    "Привет! Это Personal AI OS — твой мультиагентный ассистент в Telegram.\n"
    "Ниже первый вопрос онбординга (≈5 минут)."
)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ctx: BotContext = context.application.bot_data["ctx"]
    tg_user = update.effective_user
    if not tg_user or not update.effective_chat:
        return
    pool = ctx.pool
    redis = ctx.redis
    meta = ctx.meta
    assert meta is not None

    from personal_ai_os.db import queries
    from personal_ai_os.services import onboarding

    async with pool.acquire() as conn:
        user = await queries.get_user_by_telegram(conn, tg_user.id)
        ref = None
        if context.args:
            ref = context.args[0]
        if user is None:
            user = await queries.create_user(
                conn,
                telegram_id=tg_user.id,
                username=tg_user.username,
                full_name=tg_user.full_name,
                referral_from_code=ref,
            )

        if not user.onboarding_complete:
            await onboarding.begin_onboarding(redis, user.id)
            await update.effective_chat.send_message(f"{WELCOME}\n\n{onboarding.QUESTIONS[0]}")
        else:
            await update.effective_chat.send_message(
                "С возвращением! Пиши запрос в чат или /help.",
            )


async def cmd_setup(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ctx: BotContext = context.application.bot_data["ctx"]
    if not update.effective_chat or not update.effective_user:
        return
    from personal_ai_os.db import queries
    from personal_ai_os.services import onboarding

    async with ctx.pool.acquire() as conn:
        user = await queries.get_user_by_telegram(conn, update.effective_user.id)
        if not user:
            await update.effective_chat.send_message("Сначала /start")
            return
        await queries.update_user_profile(conn, user.id, onboarding_complete=False)
    await onboarding.begin_onboarding(ctx.redis, user.id)
    await update.effective_chat.send_message(onboarding.QUESTIONS[0])


async def cmd_agents(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ctx: BotContext = context.application.bot_data["ctx"]
    if not update.effective_chat or not update.effective_user:
        return
    from personal_ai_os.db import queries

    async with ctx.pool.acquire() as conn:
        user = await queries.get_user_by_telegram(conn, update.effective_user.id)
        if not user:
            await update.effective_chat.send_message("Сначала /start")
            return
        agents = await queries.list_agents(conn, user.id)
    if not agents:
        await update.effective_chat.send_message("Пока нет агентов — /start")
        return
    lines = [f"• {a.name} (`{a.id}`) — {a.agent_type}, active={a.is_active}" for a in agents]
    await update.effective_chat.send_message("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


async def cmd_people(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ctx: BotContext = context.application.bot_data["ctx"]
    if not update.effective_chat or not update.effective_user:
        return
    from personal_ai_os.db import queries

    async with ctx.pool.acquire() as conn:
        user = await queries.get_user_by_telegram(conn, update.effective_user.id)
        if not user:
            await update.effective_chat.send_message("Сначала /start")
            return
        people = await queries.list_people(conn, user.id)
    if not people:
        await update.effective_chat.send_message("Список пуст. Скажи: «запомни, что ...»")
        return
    lines = []
    for p in people:
        lines.append(f"• {p.name} — ДР: {p.birthday or '—'} — {p.relation or ''}")
    await update.effective_chat.send_message("\n".join(lines))


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ctx: BotContext = context.application.bot_data["ctx"]
    if not update.effective_chat or not update.effective_user:
        return
    from personal_ai_os.db import queries

    async with ctx.pool.acquire() as conn:
        user = await queries.get_user_by_telegram(conn, update.effective_user.id)
        if not user:
            await update.effective_chat.send_message("Сначала /start")
            return
        n = await queries.count_active_agents(conn, user.id)
    from personal_ai_os.config import get_settings

    unlimited = user.telegram_id in get_settings().unlimited_telegram_id_set
    eff = user.daily_token_limit + user.token_balance
    used = user.daily_tokens_used
    token_line = (
        "Токены: без дневного лимита (учёт в логах ведётся).\n"
        if unlimited
        else f"Токены сегодня: {used} / {eff}\n"
    )
    await update.effective_chat.send_message(
        f"Тариф: **{user.plan}**\n"
        f"{token_line}"
        f"Активных агентов: {n}\n"
        f"Бонусный баланс токенов (пакеты): {user.token_balance}"
        + (
            f"\nЗапланирован переход на `{user.pending_plan}` после {user.plan_expires_at}."
            if user.pending_plan and user.plan_expires_at
            else ""
        ),
        parse_mode=ParseMode.MARKDOWN,
    )


async def cmd_delete_my_data(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ctx: BotContext = context.application.bot_data["ctx"]
    if not update.effective_chat or not update.effective_user:
        return
    from personal_ai_os.db import queries
    from personal_ai_os.core.session_store import clear_session

    async with ctx.pool.acquire() as conn:
        user = await queries.get_user_by_telegram(conn, update.effective_user.id)
        if user:
            await queries.delete_user_data(conn, user.id)
            await clear_session(ctx.redis, user.id)
    await update.effective_chat.send_message("Все ваши данные удалены из системы.")


async def cmd_export_my_data(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ctx: BotContext = context.application.bot_data["ctx"]
    if not update.effective_chat or not update.effective_user:
        return
    import json
    from personal_ai_os.db import queries

    async with ctx.pool.acquire() as conn:
        user = await queries.get_user_by_telegram(conn, update.effective_user.id)
        if not user:
            await update.effective_chat.send_message("Нет данных.")
            return
        agents = await queries.list_agents(conn, user.id)
        people = await queries.list_people(conn, user.id)
    payload = {
        "user": user.model_dump(mode="json"),
        "agents": [a.model_dump(mode="json") for a in agents],
        "people": [p.model_dump(mode="json") for p in people],
    }
    raw = json.dumps(payload, ensure_ascii=False, default=str)[:3500]
    await update.effective_chat.send_message(f"```json\n{raw}\n```", parse_mode=ParseMode.MARKDOWN)


async def cmd_history(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ctx: BotContext = context.application.bot_data["ctx"]
    if not update.effective_chat or not update.effective_user:
        return
    from personal_ai_os.db import queries

    async with ctx.pool.acquire() as conn:
        user = await queries.get_user_by_telegram(conn, update.effective_user.id)
        if not user:
            await update.effective_chat.send_message("Сначала /start")
            return
        rows = await queries.recent_history(conn, user.id, 20)
    if not rows:
        await update.effective_chat.send_message("История пуста.")
        return
    await update.effective_chat.send_message("\n".join(f"• {r}" for r in rows)[:4000])


async def cmd_upgrade(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from personal_ai_os.config import get_settings

    s = get_settings()
    lines = ["Оформите подписку в Paddle (настройте CHECKOUT_URL_* в .env):"]
    if s.checkout_url_personal:
        lines.append(f"Personal: {s.checkout_url_personal}")
    if s.checkout_url_pro:
        lines.append(f"Pro: {s.checkout_url_pro}")
    if s.checkout_url_business:
        lines.append(f"Business: {s.checkout_url_business}")
    if s.checkout_url_pkg_s or s.checkout_url_pkg_m or s.checkout_url_pkg_l:
        lines.append("")
        lines.append("Пакеты токенов:")
    if s.checkout_url_pkg_s:
        lines.append(f"Пакет S (+500k): {s.checkout_url_pkg_s}")
    if s.checkout_url_pkg_m:
        lines.append(f"Пакет M (+2M): {s.checkout_url_pkg_m}")
    if s.checkout_url_pkg_l:
        lines.append(f"Пакет L (+6M): {s.checkout_url_pkg_l}")
    await update.effective_chat.send_message("\n".join(lines))


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_chat.send_message(
        "/start — начать\n"
        "/setup — пройти онбординг снова\n"
        "/create — создать агента: /create Имя | Инструкции\n"
        "/agent_toggle — включить/выключить агента: /agent_toggle <id> on|off\n"
        "/settings — настройки (язык, TZ, напоминания)\n"
        "/forget — удалить человека из памяти: /forget Имя\n"
        "/feedback — оценка ответа: /feedback <message_ref> + или - [комментарий]\n"
        "/referral — твоя реферальная ссылка\n"
        "/agents — список агентов\n"
        "/people — люди и ДР\n"
        "/status — тариф и токены\n"
        "/upgrade — оплата\n"
        "/history — последние действия\n"
        "/delete_my_data — удалить всё\n"
        "/export_my_data — выгрузка JSON\n"
        "/help — эта справка"
    )


async def cmd_create(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ctx: BotContext = context.application.bot_data["ctx"]
    from personal_ai_os.agents.engineer_agent import run_engineer
    from personal_ai_os.db import queries

    if not update.effective_chat or not update.effective_user:
        return

    raw = " ".join(context.args or []).strip()
    if not raw:
        await update.effective_chat.send_message(
            "Формат: /create Имя агента | Что он должен делать"
        )
        return

    if "|" in raw:
        name, instructions = [x.strip() for x in raw.split("|", 1)]
    else:
        name, instructions = raw, "Помогает по запросам пользователя."

    if not name:
        await update.effective_chat.send_message("Укажи имя агента.")
        return

    prompt = (
        "Создай нового агента.\n"
        f"Название: {name}\n"
        f"Инструкции: {instructions}\n"
        "После создания кратко подтверди результат."
    )

    async with ctx.pool.acquire() as conn:
        user = await queries.get_user_by_telegram(conn, update.effective_user.id)
        if not user:
            await update.effective_chat.send_message("Сначала /start")
            return
        engineer = await queries.get_agent_by_type(conn, user.id, "engineer")
        if not engineer:
            await update.effective_chat.send_message(
                "Engineer-агент не найден. Выполни /setup."
            )
            return
        msg = await run_engineer(conn, ctx.redis, ctx.meta.claude, user, engineer, prompt)
    await update.effective_chat.send_message(msg[:4090])


async def cmd_settings(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ctx: BotContext = context.application.bot_data["ctx"]
    from personal_ai_os.db import queries

    if not update.effective_chat or not update.effective_user:
        return

    raw = " ".join(context.args or []).strip()
    async with ctx.pool.acquire() as conn:
        user = await queries.get_user_by_telegram(conn, update.effective_user.id)
        if not user:
            await update.effective_chat.send_message("Сначала /start")
            return

        if not raw:
            await update.effective_chat.send_message(
                f"Текущие настройки:\n"
                f"language={user.language}\n"
                f"timezone={user.timezone}\n"
                f"reminder_hour={user.reminder_hour}\n"
                f"meeting_reminder_minutes={user.meeting_reminder_minutes}\n\n"
                "Изменить: /settings lang=ru tz=Europe/Moscow remind=9 meeting=15"
            )
            return

        fields: dict[str, object] = {}
        for part in raw.split():
            if "=" not in part:
                continue
            k, v = part.split("=", 1)
            k = k.strip().lower()
            v = v.strip()
            if k in ("lang", "language") and v:
                fields["language"] = v
            elif k in ("tz", "timezone") and v:
                fields["timezone"] = v
            elif k in ("remind", "reminder_hour"):
                try:
                    hour = int(v)
                except ValueError:
                    continue
                if 0 <= hour <= 23:
                    fields["reminder_hour"] = hour
            elif k in ("meeting", "meeting_reminder", "meeting_reminder_minutes"):
                try:
                    mins = int(v)
                except ValueError:
                    continue
                if 0 <= mins <= 1440:
                    fields["meeting_reminder_minutes"] = mins

        if not fields:
            await update.effective_chat.send_message(
                "Не удалось распознать параметры. Пример: /settings lang=ru tz=Europe/Moscow remind=9 meeting=15"
            )
            return

        await queries.update_user_profile(conn, user.id, **fields)
        refreshed = await queries.get_user_by_id(conn, user.id)

    await update.effective_chat.send_message(
        "Настройки сохранены.\n"
        f"language={refreshed.language}\n"
        f"timezone={refreshed.timezone}\n"
        f"reminder_hour={refreshed.reminder_hour}\n"
        f"meeting_reminder_minutes={refreshed.meeting_reminder_minutes}"
    )


async def cmd_forget(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ctx: BotContext = context.application.bot_data["ctx"]
    from personal_ai_os.db import queries

    if not update.effective_chat or not update.effective_user:
        return
    target_name = " ".join(context.args or []).strip()
    if not target_name:
        await update.effective_chat.send_message("Формат: /forget Имя")
        return

    async with ctx.pool.acquire() as conn:
        user = await queries.get_user_by_telegram(conn, update.effective_user.id)
        if not user:
            await update.effective_chat.send_message("Сначала /start")
            return
        deleted = await queries.delete_person_by_name(conn, user.id, target_name)
    if deleted:
        await update.effective_chat.send_message(f"Удалил запись(и): {deleted}")
    else:
        await update.effective_chat.send_message("Не нашёл человека с таким именем.")


async def cmd_agent_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ctx: BotContext = context.application.bot_data["ctx"]
    from personal_ai_os.db import queries

    if not update.effective_chat or not update.effective_user:
        return
    args = context.args or []
    if len(args) < 2:
        await update.effective_chat.send_message("Формат: /agent_toggle <agent_id> on|off")
        return
    agent_id, mode = args[0].strip(), args[1].strip().lower()
    active = mode in ("on", "1", "true", "enable", "enabled")

    import uuid

    try:
        aid = uuid.UUID(agent_id)
    except ValueError:
        await update.effective_chat.send_message("Некорректный agent_id")
        return

    async with ctx.pool.acquire() as conn:
        user = await queries.get_user_by_telegram(conn, update.effective_user.id)
        if not user:
            await update.effective_chat.send_message("Сначала /start")
            return
        ok = await queries.update_agent(conn, user.id, aid, is_active=active)
    await update.effective_chat.send_message("Готово." if ok else "Агент не найден.")


async def cmd_feedback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ctx: BotContext = context.application.bot_data["ctx"]
    from personal_ai_os.db import queries

    if not update.effective_chat or not update.effective_user:
        return
    args = context.args or []
    if len(args) < 2:
        await update.effective_chat.send_message(
            "Формат: /feedback <message_ref> +|- [комментарий]"
        )
        return
    message_ref = args[0].strip()
    raw_score = args[1].strip()
    score = 1 if raw_score in {"+", "👍", "up"} else -1
    if raw_score not in {"+", "-", "👍", "👎", "up", "down"}:
        await update.effective_chat.send_message("Оценка должна быть + или -")
        return
    comment = " ".join(args[2:]).strip() or None

    async with ctx.pool.acquire() as conn:
        user = await queries.get_user_by_telegram(conn, update.effective_user.id)
        if not user:
            await update.effective_chat.send_message("Сначала /start")
            return
        await queries.save_feedback(conn, user.id, message_ref, score, comment)

    await update.effective_chat.send_message("Спасибо, сохранил обратную связь.")


async def cmd_referral(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ctx: BotContext = context.application.bot_data["ctx"]
    if not update.effective_chat or not update.effective_user:
        return
    async with ctx.pool.acquire() as conn:
        from personal_ai_os.db import queries

        user = await queries.get_user_by_telegram(conn, update.effective_user.id)
        if not user:
            await update.effective_chat.send_message("Сначала /start")
            return
    bot = context.bot
    me = await bot.get_me()
    link = f"https://t.me/{me.username}?start={user.referral_code}" if me.username else user.referral_code
    await update.effective_chat.send_message(
        "Твоя реферальная ссылка:\n"
        f"{link}\n\n"
        "Бонус: +30 дней Pro после первой оплаты приглашенного пользователя."
    )
