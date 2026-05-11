from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

from personal_ai_os.bot.setup import BotContext
from personal_ai_os.core.security import looks_like_prompt_injection


_MAX_USER_TEXT_LEN = 4000  # Telegram message limit; защита от случайных гигантских payload-ов.


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ctx: BotContext = context.application.bot_data["ctx"]
    text = (update.message and update.message.text) or ""
    if not text.strip():
        return
    if len(text) > _MAX_USER_TEXT_LEN:
        text = text[:_MAX_USER_TEXT_LEN]
    tg_user = update.effective_user
    chat = update.effective_chat
    if not tg_user or not chat:
        return

    if not await ctx.rate_limiter.check(str(tg_user.id)):
        await chat.send_message("Слишком много сообщений. Подождите минуту.")
        return
    if looks_like_prompt_injection(text):
        await chat.send_message(
            "Не могу раскрывать системные инструкции или служебные промпты. "
            "Сформулируй задачу по сути, и я помогу."
        )
        return

    from personal_ai_os.db import queries
    from personal_ai_os.services import onboarding

    async with ctx.pool.acquire() as conn:
        user = await queries.get_user_by_telegram(conn, tg_user.id)
        if user is None:
            await chat.send_message("Нажми /start.")
            return

        if not user.onboarding_complete:
            reply, done = await onboarding.handle_onboarding_message(conn, ctx.redis, user, text)
            await chat.send_message(reply)
            return

        eff = user.daily_token_limit + user.token_balance
        if user.daily_tokens_used >= eff:
            await chat.send_message("Дневной лимит токенов исчерпан. /status или /upgrade.")
            return

        if eff > 0 and user.daily_tokens_used >= int(0.8 * eff) and user.daily_tokens_used < eff:
            await chat.send_message(
                "Ты близок к дневному лимиту токенов. /status — подробности, /upgrade — тарифы."
            )

        try:
            msg = await ctx.meta.handle_message(conn, user, text)
        except Exception:
            await chat.send_message(
                "Сервис ИИ временно недоступен. Я уже попробовал повторить запрос. Попробуйте через минуту."
            )
            return
        await chat.send_message(msg[:4090])
