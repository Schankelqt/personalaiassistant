from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import redis.asyncio as aioredis
from fastapi import FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import PlainTextResponse
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters

from personal_ai_os.agents.meta_agent import MetaAgentService
from personal_ai_os.bot.handlers import commands, messages
from personal_ai_os.bot.middleware.rate_limiter import RateLimiter
from personal_ai_os.bot.setup import BotContext
from personal_ai_os.config import get_settings
from personal_ai_os.core.claude_client import ClaudeClient
from personal_ai_os.core.crypto import encrypt
from personal_ai_os.db import queries
from personal_ai_os.db.pool import close_pool, get_pool


def build_pt_app(ctx: BotContext) -> Application:
    app = Application.builder().token(get_settings().telegram_bot_token).build()
    app.bot_data["ctx"] = ctx
    app.add_handler(CommandHandler("start", commands.cmd_start))
    app.add_handler(CommandHandler("setup", commands.cmd_setup))
    app.add_handler(CommandHandler("create", commands.cmd_create))
    app.add_handler(CommandHandler("agent_toggle", commands.cmd_agent_toggle))
    app.add_handler(CommandHandler("settings", commands.cmd_settings))
    app.add_handler(CommandHandler("forget", commands.cmd_forget))
    app.add_handler(CommandHandler("feedback", commands.cmd_feedback))
    app.add_handler(CommandHandler("referral", commands.cmd_referral))
    app.add_handler(CommandHandler("agents", commands.cmd_agents))
    app.add_handler(CommandHandler("skills", commands.cmd_skills))
    app.add_handler(CommandHandler("skill", commands.cmd_skill))
    app.add_handler(CommandHandler("people", commands.cmd_people))
    app.add_handler(CommandHandler("status", commands.cmd_status))
    app.add_handler(CommandHandler("delete_my_data", commands.cmd_delete_my_data))
    app.add_handler(CommandHandler("export_my_data", commands.cmd_export_my_data))
    app.add_handler(CommandHandler("history", commands.cmd_history))
    app.add_handler(CommandHandler("upgrade", commands.cmd_upgrade))
    app.add_handler(CommandHandler("help", commands.cmd_help))
    app.add_handler(CommandHandler("workspace", commands.cmd_workspace))
    app.add_handler(CommandHandler("link_workspace", commands.cmd_link_workspace))
    app.add_handler(CommandHandler("topic", commands.cmd_topic))
    app.add_handler(CommandHandler("topics", commands.cmd_topics))
    # В топиках форума slash-команда иногда приходит без entity «bot_command»
    _ws_cmd = filters.Regex(
        r"(?i)^/(link_workspace|topics|workspace)(@\w+)?\s*$|"
        r"^/topic(@\w+)?(\s+.+|.+)?$"
    )
    app.add_handler(MessageHandler(_ws_cmd, commands.cmd_workspace_router), group=0)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, messages.on_text))
    return app


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    if settings.sentry_dsn:
        try:
            import sentry_sdk

            sentry_sdk.init(dsn=settings.sentry_dsn)
        except Exception:
            pass

    pool = await get_pool()
    redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    claude = ClaudeClient(settings)
    meta = MetaAgentService(claude, redis)
    limiter = RateLimiter(redis, settings.rate_limit_per_minute)
    ctx = BotContext(pool=pool, redis=redis, meta=meta, rate_limiter=limiter)
    ptb = build_pt_app(ctx)
    await ptb.initialize()
    await ptb.start()
    app.state.ptb = ptb
    app.state.redis = redis
    yield
    await ptb.stop()
    await ptb.shutdown()
    await close_pool()
    await redis.aclose()


app = FastAPI(title="Personal AI OS", lifespan=lifespan)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/full")
async def health_full(request: Request) -> dict[str, object]:
    """Проверка зависимостей: Postgres, Redis.

    Не возвращает текст исключений наружу, чтобы не раскрывать внутренности.
    Подробности — в логах контейнера.
    """
    import logging

    logger = logging.getLogger("health")
    status: dict[str, object] = {"status": "ok"}
    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
        status["postgres"] = "ok"
    except Exception:
        logger.exception("health: postgres check failed")
        status["status"] = "degraded"
        status["postgres"] = "error"
    try:
        redis = request.app.state.redis
        await redis.ping()
        status["redis"] = "ok"
    except Exception:
        logger.exception("health: redis check failed")
        status["status"] = "degraded"
        status["redis"] = "error"
    return status


# SEC: Telegram присылает webhook'и до ~1 MiB; ограничиваем размер тела явно,
# чтобы исключить DoS-нагрузку через большие payload-ы.
_MAX_WEBHOOK_BODY_BYTES = 1_048_576  # 1 MiB
_MAX_PADDLE_BODY_BYTES = 524_288  # 512 KiB


async def _read_body_with_limit(request: Request, limit: int) -> bytes:
    raw = await request.body()
    if len(raw) > limit:
        raise HTTPException(status_code=413, detail="payload too large")
    return raw


@app.post("/webhook")
async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: str | None = Header(None),
) -> dict[str, bool]:
    settings = get_settings()
    # SEC: timing-safe сравнение секрета вместо обычного `!=`,
    # чтобы исключить угадывание токена по разнице во времени отклика.
    provided = x_telegram_bot_api_secret_token or ""
    expected = settings.telegram_webhook_secret
    if not hmac.compare_digest(provided.encode(), expected.encode()):
        raise HTTPException(status_code=403, detail="invalid secret")
    raw = await _read_body_with_limit(request, _MAX_WEBHOOK_BODY_BYTES)
    try:
        data = json.loads(raw.decode() or "{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise HTTPException(status_code=400, detail="invalid json") from None
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="invalid payload")
    uid = data.get("update_id")
    redis = request.app.state.redis
    if uid is not None:
        ok = await redis.set(f"upd:{uid}", "1", nx=True, ex=300)
        if not ok:
            return {"ok": True}
    ptb: Application = request.app.state.ptb
    update = Update.de_json(data, ptb.bot)
    try:
        await ptb.process_update(update)
    except Exception:
        import logging

        logging.getLogger("webhook").exception(
            "process_update failed update_id=%s",
            data.get("update_id"),
        )
        raise
    return {"ok": True}


def _paddle_verify(raw_body: bytes, signature: str | None, secret: str) -> bool:
    """Проверка подписи Paddle.

    Реальный формат заголовка `Paddle-Signature`: `ts=1700000000;h1=hexdigest`.
    HMAC вычисляется от строки `f"{ts}:{body}"` с секретом из дашборда Paddle.
    Допускаем оба варианта (formal `ts;h1` и legacy raw hex) — для совместимости
    с тестовыми утилитами разработчика и реальной интеграцией.
    """
    if not signature or not secret:
        return False
    body = raw_body
    pairs: dict[str, str] = {}
    for chunk in signature.split(";"):
        if "=" in chunk:
            k, v = chunk.split("=", 1)
            pairs[k.strip()] = v.strip()
    if "ts" in pairs and "h1" in pairs:
        ts = pairs["ts"]
        provided = pairs["h1"]
        signed_payload = f"{ts}:".encode() + body
        digest = hmac.new(secret.encode(), signed_payload, hashlib.sha256).hexdigest()
        return hmac.compare_digest(digest, provided)
    # Fallback: raw hex без timestamp (только если нет ts/h1).
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(digest, signature.strip())


@app.post("/webhooks/paddle")
async def paddle_webhook(request: Request) -> Response:
    settings = get_settings()
    # SEC: webhook без настроенного секрета — отключён полностью.
    # Иначе любой может отправить POST и назначить себе платный тариф.
    if not settings.paddle_webhook_secret:
        raise HTTPException(status_code=503, detail="paddle webhook disabled")
    raw = await _read_body_with_limit(request, _MAX_PADDLE_BODY_BYTES)
    sig = request.headers.get("paddle-signature") or request.headers.get("Paddle-Signature")
    if not _paddle_verify(raw, sig, settings.paddle_webhook_secret):
        raise HTTPException(403, "bad signature")
    try:
        payload = json.loads(raw.decode() or "{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise HTTPException(status_code=400, detail="invalid json") from None
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="invalid payload")
    event = payload.get("event_type") or payload.get("event")

    custom = payload.get("data", {}).get("custom_data") or payload.get("custom_data") or {}
    user_s = custom.get("user_id")
    price_id = None
    items = payload.get("data", {}).get("items") or []
    if items:
        price_id = items[0].get("price_id") or items[0].get("price", {}).get("id")

    pool = await get_pool()
    async with pool.acquire() as conn:
        if user_s:
            try:
                user_uuid = uuid.UUID(str(user_s))
            except ValueError:
                user_uuid = None
            if user_uuid:
                # SEC: защита от replay (повторной доставки webhook).
                # Если такой tx_id уже обработан — игнорируем без побочных эффектов.
                tx_id_check = None
                if isinstance(payload.get("data"), dict):
                    tx_id_check = payload["data"].get("id")
                if tx_id_check:
                    seen = await conn.fetchval(
                        "SELECT 1 FROM billing_events WHERE paddle_tx_id = $1",
                        str(tx_id_check),
                    )
                    if seen:
                        return PlainTextResponse("ok")
                plan_str = "free"
                if price_id and price_id == settings.paddle_price_personal:
                    plan_str = "personal"
                elif price_id and price_id == settings.paddle_price_pro:
                    plan_str = "pro"
                elif price_id and price_id == settings.paddle_price_business:
                    plan_str = "business"
                if plan_str != "free":
                    await queries.apply_plan(conn, user_uuid, plan_str)
                # Разовые токен-пакеты (поверх тарифа).
                paid_event = plan_str != "free"
                if price_id and price_id == settings.paddle_price_pkg_s:
                    await queries.add_token_balance(conn, user_uuid, 500_000)
                    paid_event = True
                elif price_id and price_id == settings.paddle_price_pkg_m:
                    await queries.add_token_balance(conn, user_uuid, 2_000_000)
                    paid_event = True
                elif price_id and price_id == settings.paddle_price_pkg_l:
                    await queries.add_token_balance(conn, user_uuid, 6_000_000)
                    paid_event = True
                await queries.insert_billing_event(
                    conn,
                    user_uuid,
                    str(event),
                    plan_str,
                    None,
                    str(tx_id_check) if tx_id_check else None,
                    {"raw_event": payload},
                )
                # BILL-07: +30 дней Pro для пригласившего после первой оплаты реферала.
                if paid_event:
                    await queries.apply_referral_bonus_if_eligible(conn, user_uuid)
                # BILL-06: отмена подписки действует в конце оплаченного периода.
                if str(event).lower() in {
                    "subscription.canceled",
                    "subscription_cancelled",
                    "subscription.updated",
                }:
                    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
                    status = str(data.get("status") or "").lower()
                    end_s = (
                        data.get("current_billing_period", {}).get("ends_at")
                        if isinstance(data.get("current_billing_period"), dict)
                        else None
                    ) or data.get("next_billed_at")
                    if status in {"canceled", "cancelled"} and end_s:
                        try:
                            eff = datetime.fromisoformat(str(end_s).replace("Z", "+00:00"))
                        except ValueError:
                            eff = None
                        if eff and eff <= datetime.now(timezone.utc):
                            await queries.apply_plan(conn, user_uuid, "free")
                        else:
                            await queries.schedule_plan_change(conn, user_uuid, "free", eff)

    return PlainTextResponse("ok")


def _safe_int(value: object, default: int) -> int:
    """Безопасный парсинг чисел из внешних ответов (OAuth provider)."""
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


@app.get("/oauth/callback/google")
async def oauth_google_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
) -> PlainTextResponse:
    import logging
    from datetime import datetime, timedelta, timezone

    import httpx

    logger = logging.getLogger("oauth")

    if not code or not state:
        raise HTTPException(400, "missing params")
    settings = get_settings()
    redis = request.app.state.redis
    user_s = await redis.get(f"oauth_state:{state}")
    if not user_s:
        raise HTTPException(400, "state")
    try:
        user_id = uuid.UUID(user_s)
    except (ValueError, TypeError):
        await redis.delete(f"oauth_state:{state}")
        raise HTTPException(400, "state") from None
    async with httpx.AsyncClient(timeout=40) as client:
        r = await client.post(
            "https://oauth2.googleapis.com/token",
            data={
                "code": code,
                "client_id": settings.google_client_id,
                "client_secret": settings.google_client_secret,
                "redirect_uri": settings.google_redirect_uri,
                "grant_type": "authorization_code",
            },
        )
        if r.status_code >= 400:
            # SEC: тело ответа Google может содержать наш client_secret и токены.
            # Логируем в наш лог, но НЕ возвращаем пользователю.
            logger.warning(
                "google oauth exchange failed: status=%s", r.status_code
            )
            raise HTTPException(400, "oauth exchange failed")
        try:
            tok = r.json()
        except ValueError:
            raise HTTPException(400, "oauth bad response") from None
        if not isinstance(tok, dict) or not tok.get("access_token"):
            raise HTTPException(400, "oauth bad response")
    key = settings.oauth_encryption_key_hex
    exp = datetime.now(timezone.utc) + timedelta(seconds=_safe_int(tok.get("expires_in"), 3600))
    pool = await get_pool()
    async with pool.acquire() as conn:
        await queries.upsert_oauth(
            conn,
            user_id,
            "google",
            encrypt(tok["access_token"], key),
            encrypt(tok.get("refresh_token", "") or " ", key),
            exp,
            tok.get("scope"),
            None,
            None,
        )
    await redis.delete(f"oauth_state:{state}")
    return PlainTextResponse("Google Calendar подключён. Вернись в Telegram.")


@app.get("/oauth/callback/jira")
async def oauth_jira_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
) -> PlainTextResponse:
    import logging
    from datetime import datetime, timedelta, timezone

    import httpx

    logger = logging.getLogger("oauth")

    if not code or not state:
        raise HTTPException(400, "missing params")
    settings = get_settings()
    redis = request.app.state.redis
    user_s = await redis.get(f"oauth_state:{state}")
    if not user_s:
        raise HTTPException(400, "state")
    try:
        user_id = uuid.UUID(user_s)
    except (ValueError, TypeError):
        await redis.delete(f"oauth_state:{state}")
        raise HTTPException(400, "state") from None
    async with httpx.AsyncClient(timeout=40) as client:
        r = await client.post(
            "https://auth.atlassian.com/oauth/token",
            json={
                "grant_type": "authorization_code",
                "client_id": settings.jira_client_id,
                "client_secret": settings.jira_client_secret,
                "code": code,
                "redirect_uri": settings.jira_redirect_uri,
            },
        )
        if r.status_code >= 400:
            logger.warning("jira oauth exchange failed: status=%s", r.status_code)
            raise HTTPException(400, "oauth exchange failed")
        try:
            tok = r.json()
        except ValueError:
            raise HTTPException(400, "oauth bad response") from None
        if not isinstance(tok, dict) or not tok.get("access_token"):
            raise HTTPException(400, "oauth bad response")
    access = tok["access_token"]
    refresh = tok.get("refresh_token", "")
    exp = datetime.now(timezone.utc) + timedelta(seconds=_safe_int(tok.get("expires_in"), 3600))
    cloud_id = None
    base = None
    async with httpx.AsyncClient(timeout=40) as client:
        rr = await client.get(
            "https://api.atlassian.com/oauth/token/accessible-resources",
            headers={"Authorization": f"Bearer {access}", "Accept": "application/json"},
        )
        if rr.status_code == 200:
            resources = rr.json()
            if resources:
                cloud_id = resources[0].get("id")
                base = resources[0].get("url")
    key = settings.oauth_encryption_key_hex
    pool = await get_pool()
    async with pool.acquire() as conn:
        await queries.upsert_oauth(
            conn,
            user_id,
            "jira",
            encrypt(access, key),
            encrypt(refresh, key),
            exp,
            tok.get("scope"),
            cloud_id,
            base,
        )
    await redis.delete(f"oauth_state:{state}")
    return PlainTextResponse("Jira подключена. Вернись в Telegram.")
