from __future__ import annotations

import logging
import re

from telegram import InputFile, Update
from telegram.constants import ChatType
from telegram.ext import ContextTypes

from personal_ai_os.agents.meta_agent import dispatch_sub_agent
from personal_ai_os.bot.setup import BotContext
from personal_ai_os.config import get_settings
from personal_ai_os.core.message_attachments import attachments_begin, attachments_drain
from personal_ai_os.core.security import looks_like_prompt_injection
from personal_ai_os.services import telegram_forum


_MAX_USER_TEXT_LEN = 4000


def _topic_request_pattern(text: str) -> bool:
    t = text.lower().strip()
    if re.match(r"(?i)^/topic(@\w+)?(\s+.+|.+)?$", t):
        return True
    return bool(
        re.search(
            r"(открой|создай|новый)\s+(топик|тему)|"
            r"изолируй\s+агент",
            t,
        )
    )


async def _send_reply(
    update: Update,
    chat,
    text: str,
    *,
    thread_id: int | None,
    files: list[tuple[str, bytes]],
) -> None:
    kwargs: dict = {}
    if thread_id is not None:
        kwargs["message_thread_id"] = thread_id
    await chat.send_message(text[:4090], **kwargs)
    for fname, raw in files:
        try:
            await chat.send_document(
                document=InputFile(raw, filename=fname),
                caption=f"Файл: {fname}",
                **kwargs,
            )
        except Exception:
            logging.getLogger(__name__).exception("send_document failed file=%s", fname)


async def _check_token_budget(user, tg_user_id: int) -> str | None:
    unlimited = tg_user_id in get_settings().unlimited_telegram_id_set
    eff = user.daily_token_limit + user.token_balance
    if not unlimited and user.daily_tokens_used >= eff:
        return "Дневной лимит токенов исчерпан. /status или /upgrade."
    return None


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger = logging.getLogger(__name__)
    ctx: BotContext = context.application.bot_data["ctx"]
    message = update.message
    text = (message and message.text) or ""
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

    thread_id = message.message_thread_id if message else None
    chat_id = chat.id
    is_group = chat.type in (ChatType.GROUP, ChatType.SUPERGROUP)

    async with ctx.pool.acquire() as conn:
        user = await queries.get_user_by_telegram(conn, tg_user.id)
        if user is None:
            await chat.send_message("Нажми /start.")
            return

        if not user.onboarding_complete:
            if is_group:
                await chat.send_message(
                    "Онбординг — в личке с ботом: /start",
                    message_thread_id=thread_id,
                )
                return
            reply, _done = await onboarding.handle_onboarding_message(conn, ctx.redis, user, text)
            await chat.send_message(reply)
            return

        budget_msg = await _check_token_budget(user, tg_user.id)
        if budget_msg:
            await chat.send_message(budget_msg, message_thread_id=thread_id)
            return

        unlimited = tg_user.id in get_settings().unlimited_telegram_id_set
        eff = user.daily_token_limit + user.token_balance
        if (
            not unlimited
            and eff > 0
            and user.daily_tokens_used >= int(0.8 * eff)
            and user.daily_tokens_used < eff
            and not is_group
        ):
            await chat.send_message(
                "Ты близок к дневному лимиту токенов. /status — подробности, /upgrade — тарифы."
            )

        bot = context.bot
        attachments_begin()

        # --- Group / supergroup (forum topics optional) ---
        if is_group:
            workspace_chat = await queries.get_user_workspace_chat_id(conn, user.id)
            linked_here = workspace_chat is not None and workspace_chat == chat_id

            if _topic_request_pattern(text):
                from personal_ai_os.bot.handlers.commands import handle_topic_command

                await handle_topic_command(update, context, user, text)
                return

            bound_agent = None
            use_meta = True

            if linked_here:
                ws_user, bound_agent, use_meta = await telegram_forum.resolve_agent_for_update(
                    conn, chat_id, thread_id, tg_user.id
                )
                # Чужой пользователь в привязанной группе — игнорируем
                if ws_user is None:
                    return
                user = ws_user
            else:
                link_hint = (
                    "\n\n_Чтобы изолировать агентов в отдельных топиках: "
                    "включи Topics, дай боту право Manage Topics и отправь "
                    "`/link_workspace` в этой группе._"
                )
            try:
                if bound_agent is not None and not use_meta:
                    msg = await dispatch_sub_agent(
                        conn,
                        ctx.redis,
                        ctx.meta.claude,
                        user,
                        bound_agent,
                        text,
                        thread_id=thread_id,
                        bot=bot,
                    )
                else:
                    msg = await ctx.meta.handle_message(conn, user, text)
                    if not linked_here:
                        msg = (msg or "") + link_hint
            except Exception:
                logger.exception("group message failed user_id=%s", user.id)
                msg = "Сервис ИИ временно недоступен. Попробуйте через минуту."
            finally:
                files = attachments_drain()
            await _send_reply(update, chat, msg, thread_id=thread_id, files=files)
            return

        # --- Private chat ---
        try:
            if _topic_request_pattern(text):
                from personal_ai_os.bot.handlers.commands import handle_topic_command

                await handle_topic_command(update, context, user, text)
                return

            msg = await ctx.meta.handle_message(conn, user, text)
        except Exception:
            logger.exception("meta handle_message failed: user_id=%s", user.id)
            msg = (
                "Сервис ИИ временно недоступен. Я уже попробовал повторить запрос. "
                "Попробуйте через минуту."
            )
        finally:
            files = attachments_drain()
        await _send_reply(update, chat, msg, thread_id=None, files=files)
