from __future__ import annotations

import logging
import re
from typing import Any

from telegram import InputFile, Update
from telegram.constants import ChatType
from telegram.ext import ContextTypes

from personal_ai_os.agents.meta_agent import dispatch_sub_agent
from personal_ai_os.bot.setup import BotContext
from personal_ai_os.core.file_ingest import format_ingest_for_prompt, ingest_file
from personal_ai_os.core.message_attachments import attachments_begin, attachments_drain
from personal_ai_os.core.security import looks_like_prompt_injection
from personal_ai_os.services import telegram_forum

logger = logging.getLogger(__name__)

_MAX_USER_TEXT_LEN = 4000
_IMAGE_MEDIA = {
    "image/jpeg": "image/jpeg",
    "image/png": "image/png",
    "image/webp": "image/webp",
    "image/gif": "image/gif",
}


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
            logger.exception("send_document failed file=%s", fname)


async def _check_token_budget(user, tg_user_id: int) -> str | None:
    from personal_ai_os.core.limits import limits_disabled, user_has_unlimited_tokens

    if limits_disabled() or user_has_unlimited_tokens(user):
        return None
    eff = user.daily_token_limit + user.token_balance
    if user.daily_tokens_used >= eff:
        return "Дневной лимит токенов исчерпан. /status или /upgrade."
    return None


async def _download_file_bytes(bot, file_id: str) -> bytes:
    tg_file = await bot.get_file(file_id)
    data = await tg_file.download_as_bytearray()
    return bytes(data)


def _guess_image_media_type(filename: str | None, mime: str | None) -> str:
    if mime and mime in _IMAGE_MEDIA:
        return mime
    if filename and "." in filename:
        ext = filename.rsplit(".", 1)[-1].lower()
        if ext in ("jpg", "jpeg"):
            return "image/jpeg"
        if ext == "png":
            return "image/png"
        if ext == "webp":
            return "image/webp"
        if ext == "gif":
            return "image/gif"
    return "image/jpeg"


async def _route_user_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    text: str,
    image_parts: list[tuple[str, bytes]] | None = None,
) -> None:
    ctx: BotContext = context.application.bot_data["ctx"]
    message = update.message
    tg_user = update.effective_user
    chat = update.effective_chat
    if not tg_user or not chat or not message:
        return

    if len(text) > _MAX_USER_TEXT_LEN:
        text = text[:_MAX_USER_TEXT_LEN]

    from personal_ai_os.core.limits import limits_disabled

    if not limits_disabled() and not await ctx.rate_limiter.check(str(tg_user.id)):
        await chat.send_message("Слишком много сообщений. Подождите минуту.")
        return
    if text.strip() and looks_like_prompt_injection(text):
        await chat.send_message(
            "Не могу раскрывать системные инструкции или служебные промпты. "
            "Сформулируй задачу по сути, и я помогу."
        )
        return

    from personal_ai_os.db import queries
    from personal_ai_os.services import onboarding

    thread_id = message.message_thread_id
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
            reply, _done = await onboarding.handle_onboarding_message(
                conn, ctx.redis, user, text, claude=ctx.meta.claude
            )
            await chat.send_message(reply, message_thread_id=thread_id)
            return

        budget_msg = await _check_token_budget(user, tg_user.id)
        if budget_msg:
            await chat.send_message(budget_msg, message_thread_id=thread_id)
            return

        from personal_ai_os.core.limits import user_has_unlimited_tokens

        eff = user.daily_token_limit + user.token_balance
        if (
            not user_has_unlimited_tokens(user)
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
        link_hint = ""

        if is_group:
            workspace_chat = await queries.get_user_workspace_chat_id(conn, user.id)
            linked_here = workspace_chat is not None and workspace_chat == chat_id

            if text.strip() and _topic_request_pattern(text):
                from personal_ai_os.bot.handlers.commands import handle_topic_command

                await handle_topic_command(update, context, user, text)
                return

            bound_agent = None
            use_meta = True

            if linked_here:
                ws_user, bound_agent, use_meta = await telegram_forum.resolve_agent_for_update(
                    conn, chat_id, thread_id, tg_user.id
                )
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
                        auto_create_topic=False,
                    )
                else:
                    msg = await ctx.meta.handle_message(
                        conn, user, text, bot=bot, image_parts=image_parts
                    )
                    if not linked_here:
                        msg = (msg or "") + link_hint
            except Exception:
                logger.exception("group message failed user_id=%s", user.id)
                msg = "Сервис ИИ временно недоступен. Попробуйте через минуту."
            finally:
                files = attachments_drain()
            await _send_reply(update, chat, msg, thread_id=thread_id, files=files)
            return

        try:
            if text.strip() and _topic_request_pattern(text):
                from personal_ai_os.bot.handlers.commands import handle_topic_command

                await handle_topic_command(update, context, user, text)
                return

            msg = await ctx.meta.handle_message(
                conn, user, text, bot=bot, image_parts=image_parts
            )
        except Exception:
            logger.exception("meta handle_message failed: user_id=%s", user.id)
            msg = (
                "Сервис ИИ временно недоступен. Я уже попробовал повторить запрос. "
                "Попробуйте через минуту."
            )
        finally:
            files = attachments_drain()
        await _send_reply(update, chat, msg, thread_id=None, files=files)


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    text = (message and message.text) or ""
    if not text.strip():
        return
    await _route_user_message(update, context, text=text)


async def on_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    if not message or not message.document:
        return
    doc = message.document
    caption = (message.caption or "").strip()
    filename = doc.file_name or "file.bin"
    mime = doc.mime_type or ""

    try:
        raw = await _download_file_bytes(context.bot, doc.file_id)
    except Exception:
        logger.exception("document download failed")
        await message.reply_text("Не удалось скачать файл из Telegram.")
        return

    if mime.startswith("image/") or filename.lower().endswith(
        (".png", ".jpg", ".jpeg", ".webp", ".gif")
    ):
        media = _guess_image_media_type(filename, mime)
        prompt = caption or "Пользователь прислал изображение (файл). Опиши и ответь по задаче."
        await _route_user_message(
            update,
            context,
            text=prompt,
            image_parts=[(media, raw)],
        )
        return

    ingested = ingest_file(raw, filename)
    body = format_ingest_for_prompt(ingested, user_caption=caption)
    prompt = f"{caption}\n\n{body}".strip() if caption else body
    if not prompt.strip():
        await message.reply_text("Не удалось извлечь текст из файла.")
        return
    await _route_user_message(update, context, text=prompt)


async def on_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    if not message or not message.photo:
        return
    photo = message.photo[-1]
    caption = (message.caption or "").strip()

    try:
        raw = await _download_file_bytes(context.bot, photo.file_id)
    except Exception:
        logger.exception("photo download failed")
        await message.reply_text("Не удалось скачать фото.")
        return

    prompt = caption or "Пользователь прислал скриншот/фото. Разбери изображение и ответь по задаче."
    await _route_user_message(
        update,
        context,
        text=prompt,
        image_parts=[("image/jpeg", raw)],
    )
