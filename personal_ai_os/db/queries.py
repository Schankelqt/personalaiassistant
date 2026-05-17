from __future__ import annotations

import json
import re
import secrets
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

import asyncpg

from personal_ai_os.config import get_settings
from personal_ai_os.db.models import (
    AgentRow,
    AgentTopicRow,
    KnowledgeEntryRow,
    MemoryEntryRow,
    OAuthTokenRow,
    Plan,
    UserRow,
)


def _gen_ref_code() -> str:
    return secrets.token_urlsafe(6)[:8].upper()


PLAN_DAILY_TOKENS: dict[str, int] = {
    "free": 50_000,
    "personal": 200_000,
    "pro": 1_000_000,
    "business": 2_000_000,
}


async def get_user_by_telegram(conn: asyncpg.Connection, telegram_id: int) -> UserRow | None:
    row = await conn.fetchrow("SELECT * FROM users WHERE telegram_id = $1", telegram_id)
    return UserRow.model_validate(dict(row)) if row else None


async def get_user_by_id(conn: asyncpg.Connection, user_id: uuid.UUID) -> UserRow | None:
    row = await conn.fetchrow("SELECT * FROM users WHERE id = $1", user_id)
    return UserRow.model_validate(dict(row)) if row else None


async def create_user(
    conn: asyncpg.Connection,
    telegram_id: int,
    username: str | None,
    full_name: str | None,
    referral_from_code: str | None = None,
) -> UserRow:
    referred_by: uuid.UUID | None = None
    if referral_from_code:
        r = await conn.fetchrow(
            "SELECT id FROM users WHERE referral_code = $1",
            referral_from_code.strip().upper(),
        )
        if r:
            referred_by = r["id"]

    ref = _gen_ref_code()
    # ensure uniqueness
    for _ in range(5):
        exists = await conn.fetchval("SELECT 1 FROM users WHERE referral_code = $1", ref)
        if not exists:
            break
        ref = _gen_ref_code()

    row = await conn.fetchrow(
        """
        INSERT INTO users (telegram_id, username, full_name, referral_code, referred_by)
        VALUES ($1, $2, $3, $4, $5)
        RETURNING *
        """,
        telegram_id,
        username,
        full_name,
        ref,
        referred_by,
    )
    assert row is not None
    return UserRow.model_validate(dict(row))


async def update_user_profile(
    conn: asyncpg.Connection,
    user_id: uuid.UUID,
    **fields: Any,
) -> None:
    if not fields:
        return
    keys = list(fields.keys())
    vals = [fields[k] for k in keys]
    sets = ", ".join(f"{k} = ${i + 2}" for i, k in enumerate(keys))
    await conn.execute(
        f"UPDATE users SET {sets}, updated_at = NOW() WHERE id = $1",
        user_id,
        *vals,
    )


async def list_agents(conn: asyncpg.Connection, user_id: uuid.UUID) -> list[AgentRow]:
    rows = await conn.fetch(
        "SELECT * FROM agents WHERE user_id = $1 AND is_active = true ORDER BY created_at",
        user_id,
    )
    return [AgentRow.model_validate(dict(r)) for r in rows]


async def insert_agent(
    conn: asyncpg.Connection,
    user_id: uuid.UUID,
    name: str,
    agent_type: str,
    system_prompt: str,
    tools: list[str],
    metadata: dict[str, Any] | None = None,
) -> AgentRow:
    row = await conn.fetchrow(
        """
        INSERT INTO agents (user_id, name, agent_type, system_prompt, tools, metadata)
        VALUES ($1, $2, $3, $4, $5::jsonb, $6::jsonb)
        RETURNING *
        """,
        user_id,
        name,
        agent_type,
        system_prompt,
        json.dumps(tools),
        json.dumps(metadata or {}),
    )
    assert row is not None
    return AgentRow.model_validate(dict(row))


async def update_agent(
    conn: asyncpg.Connection,
    user_id: uuid.UUID,
    agent_id: uuid.UUID,
    *,
    name: str | None = None,
    system_prompt: str | None = None,
    is_active: bool | None = None,
    tools: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
) -> bool:
    res = await conn.execute(
        """
        UPDATE agents SET
          name = COALESCE($3, name),
          system_prompt = COALESCE($4, system_prompt),
          is_active = COALESCE($5, is_active),
          tools = COALESCE($6::jsonb, tools),
          metadata = COALESCE($7::jsonb, metadata)
        WHERE user_id = $1 AND id = $2
        """,
        user_id,
        agent_id,
        name,
        system_prompt,
        is_active,
        json.dumps(tools) if tools is not None else None,
        json.dumps(metadata) if metadata is not None else None,
    )
    return res.endswith("1")


async def get_agent(conn: asyncpg.Connection, user_id: uuid.UUID, agent_id: uuid.UUID) -> AgentRow | None:
    row = await conn.fetchrow(
        "SELECT * FROM agents WHERE user_id = $1 AND id = $2",
        user_id,
        agent_id,
    )
    return AgentRow.model_validate(dict(row)) if row else None


async def get_agent_by_type(
    conn: asyncpg.Connection,
    user_id: uuid.UUID,
    agent_type: str,
) -> AgentRow | None:
    row = await conn.fetchrow(
        """
        SELECT * FROM agents
        WHERE user_id = $1 AND agent_type = $2 AND is_active = true
        ORDER BY created_at
        LIMIT 1
        """,
        user_id,
        agent_type,
    )
    return AgentRow.model_validate(dict(row)) if row else None


async def count_active_agents(conn: asyncpg.Connection, user_id: uuid.UUID) -> int:
    return int(
        await conn.fetchval(
            "SELECT COUNT(*) FROM agents WHERE user_id = $1 AND is_active = true",
            user_id,
        )
    )


async def count_custom_agents(conn: asyncpg.Connection, user_id: uuid.UUID) -> int:
    return int(
        await conn.fetchval(
            """
            SELECT COUNT(*) FROM agents
            WHERE user_id = $1 AND is_active = true AND agent_type = 'custom'
            """,
            user_id,
        )
    )


def max_agents_for_plan(plan: str) -> int:
    """Максимум агентов всего (для отображения / мягких проверок)."""
    if plan == "free":
        return 4
    if plan == "personal":
        return 5
    return 999


def max_custom_agents_for_plan(plan: str) -> int:
    """Сколько своих (custom) агентов можно добавить поверх Engineer/Memory/Work."""
    if plan == "free":
        return 1
    if plan == "personal":
        return 4
    return 999


def can_add_custom_agent(plan: str, custom_count: int) -> bool:
    from personal_ai_os.core.limits import limits_disabled

    if limits_disabled():
        return True
    return custom_count < max_custom_agents_for_plan(plan)


async def log_tokens(
    conn: asyncpg.Connection,
    user_id: uuid.UUID,
    agent_id: uuid.UUID | None,
    model: str | None,
    input_tokens: int,
    output_tokens: int,
) -> None:
    await conn.execute(
        """
        INSERT INTO token_logs (user_id, agent_id, model, input_tokens, output_tokens)
        VALUES ($1, $2, $3, $4, $5)
        """,
        user_id,
        agent_id,
        model,
        input_tokens,
        output_tokens,
    )


async def try_consume_tokens(conn: asyncpg.Connection, user: UserRow, n: int) -> bool:
    """Reserve n tokens; returns False if budget exceeded."""
    from personal_ai_os.core.limits import limits_disabled, user_has_unlimited_tokens

    if n <= 0:
        return True
    if limits_disabled() or user_has_unlimited_tokens(user):
        return True
    row = await conn.fetchrow("SELECT daily_tokens_used, token_balance FROM users WHERE id = $1 FOR UPDATE", user.id)
    if row is None:
        return False
    daily_used = row["daily_tokens_used"]
    balance = row["token_balance"]
    limit = user.daily_token_limit
    prev_overage = max(0, daily_used - limit)
    new_used = daily_used + n
    new_overage = max(0, new_used - limit)
    delta_balance = new_overage - prev_overage
    if new_used > limit + balance:
        return False
    new_balance = balance - delta_balance
    await conn.execute(
        """
        UPDATE users SET daily_tokens_used = $2, token_balance = $3, updated_at = NOW()
        WHERE id = $1
        """,
        user.id,
        new_used,
        new_balance,
    )
    return True


async def rollback_tokens(conn: asyncpg.Connection, user_id: uuid.UUID, n: int) -> None:
    """Best-effort rollback if LLM failed after reservation (rare)."""
    await conn.execute(
        """
        UPDATE users
        SET daily_tokens_used = GREATEST(daily_tokens_used - $2, 0),
            updated_at = NOW()
        WHERE id = $1
        """,
        user_id,
        n,
    )


async def delete_user_data(conn: asyncpg.Connection, user_id: uuid.UUID) -> None:
    await conn.execute("DELETE FROM users WHERE id = $1", user_id)


async def list_people(conn: asyncpg.Connection, user_id: uuid.UUID) -> list[MemoryEntryRow]:
    from datetime import date

    rows = await conn.fetch(
        """
        SELECT * FROM memory_entries
        WHERE user_id = $1 AND entry_type = 'person'
        ORDER BY birthday NULLS LAST, name ASC
        """,
        user_id,
    )
    people = [MemoryEntryRow.model_validate(dict(r)) for r in rows]
    today = date.today()

    def _days_until(bday: date | None) -> int:
        if bday is None:
            return 9999
        m = bday.month
        d = bday.day
        if m == 2 and d == 29 and not _is_leap(today.year):
            d = 28
        target = date(today.year, m, d)
        if target < today:
            ny = today.year + 1
            if m == 2 and d == 29 and not _is_leap(ny):
                d = 28
            target = date(ny, m, d)
        return (target - today).days

    people.sort(key=lambda p: (_days_until(p.birthday), (p.name or "").lower()))
    return people


def _is_leap(year: int) -> bool:
    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)


async def insert_memory_person(
    conn: asyncpg.Connection,
    user_id: uuid.UUID,
    name: str,
    birthday: date | None,
    relation: str | None,
    tg_username: str | None,
    notes: str | None,
) -> uuid.UUID:
    _id = uuid.uuid4()
    await conn.execute(
        """
        INSERT INTO memory_entries
        (id, user_id, entry_type, name, birthday, relation, tg_username, notes)
        VALUES ($1, $2, 'person', $3, $4, $5, $6, $7)
        """,
        _id,
        user_id,
        name,
        birthday,
        relation,
        tg_username,
        notes,
    )
    return _id


async def insert_note(
    conn: asyncpg.Connection,
    user_id: uuid.UUID,
    content: str,
    tags: list[str],
) -> uuid.UUID:
    _id = uuid.uuid4()
    await conn.execute(
        """
        INSERT INTO memory_entries (id, user_id, entry_type, content, tags)
        VALUES ($1, $2, 'note', $3, $4)
        """,
        _id,
        user_id,
        content,
        tags,
    )
    return _id


async def delete_memory_entry(conn: asyncpg.Connection, user_id: uuid.UUID, entry_id: uuid.UUID) -> bool:
    res = await conn.execute(
        "DELETE FROM memory_entries WHERE user_id = $1 AND id = $2",
        user_id,
        entry_id,
    )
    return res.endswith("1")


async def delete_person_by_name(conn: asyncpg.Connection, user_id: uuid.UUID, name: str) -> int:
    res = await conn.execute(
        """
        DELETE FROM memory_entries
        WHERE user_id = $1 AND entry_type = 'person' AND lower(name) = lower($2)
        """,
        user_id,
        name.strip(),
    )
    return int((res.split()[-1] if res else "0") or 0)


async def get_memory_entry(conn: asyncpg.Connection, user_id: uuid.UUID, entry_id: uuid.UUID) -> MemoryEntryRow | None:
    row = await conn.fetchrow(
        "SELECT * FROM memory_entries WHERE user_id = $1 AND id = $2",
        user_id,
        entry_id,
    )
    return MemoryEntryRow.model_validate(dict(row)) if row else None


async def upsert_oauth(
    conn: asyncpg.Connection,
    user_id: uuid.UUID,
    provider: str,
    access_enc: str,
    refresh_enc: str,
    expires_at: datetime | None,
    scope: str | None,
    jira_cloud_id: str | None,
    jira_base_url: str | None,
) -> None:
    await conn.execute(
        """
        INSERT INTO oauth_tokens
        (user_id, provider, access_token_enc, refresh_token_enc, expires_at, scope, jira_cloud_id, jira_base_url)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
        ON CONFLICT (user_id, provider) DO UPDATE SET
          access_token_enc = EXCLUDED.access_token_enc,
          refresh_token_enc = EXCLUDED.refresh_token_enc,
          expires_at = EXCLUDED.expires_at,
          scope = EXCLUDED.scope,
          jira_cloud_id = EXCLUDED.jira_cloud_id,
          jira_base_url = EXCLUDED.jira_base_url
        """,
        user_id,
        provider,
        access_enc,
        refresh_enc,
        expires_at,
        scope,
        jira_cloud_id,
        jira_base_url,
    )


async def get_oauth(conn: asyncpg.Connection, user_id: uuid.UUID, provider: str) -> OAuthTokenRow | None:
    row = await conn.fetchrow(
        "SELECT * FROM oauth_tokens WHERE user_id = $1 AND provider = $2",
        user_id,
        provider,
    )
    return OAuthTokenRow.model_validate(dict(row)) if row else None


async def insert_reminder(
    conn: asyncpg.Connection,
    user_id: uuid.UUID,
    reminder_type: str,
    ref_id: uuid.UUID | None,
    trigger_at: datetime,
    payload: dict[str, Any],
) -> uuid.UUID:
    _id = uuid.uuid4()
    await conn.execute(
        """
        INSERT INTO reminders (id, user_id, reminder_type, ref_id, trigger_at, payload)
        VALUES ($1, $2, $3, $4, $5, $6::jsonb)
        """,
        _id,
        user_id,
        reminder_type,
        ref_id,
        trigger_at,
        payload,
    )
    return _id


async def log_interaction(conn: asyncpg.Connection, user_id: uuid.UUID, summary: str) -> None:
    await conn.execute(
        """
        INSERT INTO interaction_history (user_id, summary)
        VALUES ($1, $2)
        """,
        user_id,
        summary,
    )


async def save_feedback(
    conn: asyncpg.Connection,
    user_id: uuid.UUID,
    message_ref: str,
    score: int,
    comment: str | None = None,
) -> None:
    await conn.execute(
        """
        INSERT INTO message_feedback (user_id, message_ref, score, comment)
        VALUES ($1, $2, $3, $4)
        """,
        user_id,
        message_ref,
        score,
        comment,
    )


async def recent_history(conn: asyncpg.Connection, user_id: uuid.UUID, limit: int = 20) -> list[str]:
    rows = await conn.fetch(
        """
        SELECT to_char(created_at AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI') AS ts, summary
        FROM interaction_history
        WHERE user_id = $1
        ORDER BY created_at DESC
        LIMIT $2
        """,
        user_id,
        limit,
    )
    return [f"[{r['ts']} UTC] {r['summary']}" for r in rows]


async def insert_billing_event(
    conn: asyncpg.Connection,
    user_id: uuid.UUID,
    event_type: str,
    plan: str | None,
    amount_usd: Decimal | None,
    paddle_tx_id: str | None,
    metadata: dict[str, Any],
) -> None:
    await conn.execute(
        """
        INSERT INTO billing_events (user_id, event_type, plan, amount_usd, paddle_tx_id, metadata)
        VALUES ($1, $2, $3, $4, $5, $6::jsonb)
        """,
        user_id,
        event_type,
        plan,
        amount_usd,
        paddle_tx_id,
        metadata,
    )


async def apply_plan(
    conn: asyncpg.Connection,
    user_id: uuid.UUID,
    plan: Plan | str,
) -> None:
    p = plan.value if isinstance(plan, Plan) else plan
    daily = PLAN_DAILY_TOKENS.get(p, PLAN_DAILY_TOKENS["free"])
    await conn.execute(
        """
        UPDATE users SET plan = $2, daily_token_limit = $3, updated_at = NOW()
        WHERE id = $1
        """,
        user_id,
        p,
        daily,
    )


async def schedule_plan_change(
    conn: asyncpg.Connection,
    user_id: uuid.UUID,
    pending_plan: Plan | str,
    effective_at: datetime | None,
) -> None:
    p = pending_plan.value if isinstance(pending_plan, Plan) else str(pending_plan)
    await conn.execute(
        """
        UPDATE users
        SET pending_plan = $2,
            plan_expires_at = $3,
            updated_at = NOW()
        WHERE id = $1
        """,
        user_id,
        p,
        effective_at,
    )


async def apply_due_plan_changes(conn: asyncpg.Connection) -> int:
    rows = await conn.fetch(
        """
        SELECT id, pending_plan
        FROM users
        WHERE pending_plan IS NOT NULL
          AND plan_expires_at IS NOT NULL
          AND plan_expires_at <= NOW()
        """
    )
    count = 0
    for r in rows:
        await apply_plan(conn, r["id"], r["pending_plan"])
        await conn.execute(
            """
            UPDATE users
            SET pending_plan = NULL, plan_expires_at = NULL, updated_at = NOW()
            WHERE id = $1
            """,
            r["id"],
        )
        count += 1
    return count


async def add_token_balance(conn: asyncpg.Connection, user_id: uuid.UUID, amount: int) -> None:
    await conn.execute(
        """
        UPDATE users SET token_balance = token_balance + $2, updated_at = NOW()
        WHERE id = $1
        """,
        user_id,
        amount,
    )


async def apply_referral_bonus_if_eligible(conn: asyncpg.Connection, paid_user_id: uuid.UUID) -> bool:
    from datetime import timedelta, timezone

    paid = await conn.fetchrow(
        """
        SELECT id, referred_by, referral_rewarded_at
        FROM users
        WHERE id = $1
        """,
        paid_user_id,
    )
    if not paid:
        return False
    if paid["referral_rewarded_at"] is not None:
        return False
    referrer_id = paid["referred_by"]
    if referrer_id is None:
        await conn.execute(
            "UPDATE users SET referral_rewarded_at = NOW(), updated_at = NOW() WHERE id = $1",
            paid_user_id,
        )
        return False

    ref = await get_user_by_id(conn, referrer_id)
    if ref is None:
        await conn.execute(
            "UPDATE users SET referral_rewarded_at = NOW(), updated_at = NOW() WHERE id = $1",
            paid_user_id,
        )
        return False

    now = datetime.now(timezone.utc)
    bonus_until = now + timedelta(days=30)

    # Даем Pro на 30 дней и планируем возврат к предыдущему тарифу.
    if ref.plan != Plan.pro:
        await apply_plan(conn, ref.id, Plan.pro)
        await schedule_plan_change(conn, ref.id, ref.plan, bonus_until)
    else:
        await conn.execute(
            """
            UPDATE users
            SET plan_expires_at = CASE
              WHEN plan_expires_at IS NULL THEN $2
              WHEN plan_expires_at < $2 THEN $2
              ELSE plan_expires_at
            END,
            pending_plan = COALESCE(pending_plan, 'pro'),
            updated_at = NOW()
            WHERE id = $1
            """,
            ref.id,
            bonus_until,
        )

    await conn.execute(
        "UPDATE users SET referral_rewarded_at = NOW(), updated_at = NOW() WHERE id = $1",
        paid_user_id,
    )
    return True


async def finalize_llm_usage(
    conn: asyncpg.Connection,
    user: UserRow,
    agent_id: uuid.UUID | None,
    model: str,
    input_tokens: int,
    output_tokens: int,
) -> bool:
    await log_tokens(conn, user.id, agent_id, model, input_tokens, output_tokens)
    return await try_consume_tokens(conn, user, input_tokens + output_tokens)


async def reset_daily_tokens_all(conn: asyncpg.Connection) -> None:
    await conn.execute("UPDATE users SET daily_tokens_used = 0, updated_at = NOW()")


async def users_for_birthday_scan(conn: asyncpg.Connection) -> list[UserRow]:
    rows = await conn.fetch("SELECT DISTINCT u.* FROM users u INNER JOIN memory_entries m ON m.user_id = u.id")
    return [UserRow.model_validate(dict(r)) for r in rows]


async def set_user_workspace(conn: asyncpg.Connection, user_id: uuid.UUID, chat_id: int) -> None:
    await conn.execute(
        """
        UPDATE users
        SET telegram_workspace_chat_id = $2, workspace_linked_at = NOW(), updated_at = NOW()
        WHERE id = $1
        """,
        user_id,
        chat_id,
    )


async def get_user_workspace_chat_id(conn: asyncpg.Connection, user_id: uuid.UUID) -> int | None:
    val = await conn.fetchval(
        "SELECT telegram_workspace_chat_id FROM users WHERE id = $1",
        user_id,
    )
    return int(val) if val is not None else None


async def get_user_by_workspace_chat(conn: asyncpg.Connection, chat_id: int) -> UserRow | None:
    row = await conn.fetchrow(
        "SELECT * FROM users WHERE telegram_workspace_chat_id = $1",
        chat_id,
    )
    return UserRow.model_validate(dict(row)) if row else None


async def insert_agent_topic(
    conn: asyncpg.Connection,
    *,
    user_id: uuid.UUID,
    agent_id: uuid.UUID,
    workspace_chat_id: int,
    telegram_thread_id: int,
    topic_title: str,
) -> AgentTopicRow:
    row = await conn.fetchrow(
        """
        INSERT INTO agent_topics (user_id, agent_id, workspace_chat_id, telegram_thread_id, topic_title)
        VALUES ($1, $2, $3, $4, $5)
        RETURNING *
        """,
        user_id,
        agent_id,
        workspace_chat_id,
        telegram_thread_id,
        topic_title,
    )
    assert row is not None
    return AgentTopicRow.model_validate(dict(row))


async def get_agent_topic_by_thread(
    conn: asyncpg.Connection,
    workspace_chat_id: int,
    telegram_thread_id: int,
) -> AgentTopicRow | None:
    row = await conn.fetchrow(
        """
        SELECT * FROM agent_topics
        WHERE workspace_chat_id = $1 AND telegram_thread_id = $2 AND status = 'active'
        """,
        workspace_chat_id,
        telegram_thread_id,
    )
    return AgentTopicRow.model_validate(dict(row)) if row else None


async def get_active_topic_for_agent(
    conn: asyncpg.Connection,
    user_id: uuid.UUID,
    agent_id: uuid.UUID,
) -> AgentTopicRow | None:
    row = await conn.fetchrow(
        """
        SELECT * FROM agent_topics
        WHERE user_id = $1 AND agent_id = $2 AND status = 'active'
        ORDER BY created_at DESC
        LIMIT 1
        """,
        user_id,
        agent_id,
    )
    return AgentTopicRow.model_validate(dict(row)) if row else None


async def list_agent_topics(conn: asyncpg.Connection, user_id: uuid.UUID) -> list[AgentTopicRow]:
    rows = await conn.fetch(
        """
        SELECT * FROM agent_topics
        WHERE user_id = $1 AND status = 'active'
        ORDER BY created_at
        """,
        user_id,
    )
    return [AgentTopicRow.model_validate(dict(r)) for r in rows]


async def search_knowledge(
    conn: asyncpg.Connection,
    user_id: uuid.UUID,
    query: str,
    *,
    limit: int = 8,
) -> list[KnowledgeEntryRow]:
    tokens = [t for t in re.findall(r"[\wа-яА-ЯёЁ]{3,}", (query or "").lower()) if t][:6]
    if not tokens:
        rows = await conn.fetch(
            """
            SELECT * FROM knowledge_entries
            WHERE scope = 'system' OR (scope = 'user' AND user_id = $1)
            ORDER BY updated_at DESC
            LIMIT $2
            """,
            user_id,
            limit,
        )
        return [KnowledgeEntryRow.model_validate(dict(r)) for r in rows]

    patterns = [f"%{t}%" for t in tokens]
    conds = " OR ".join(
        f"(title ILIKE ${i + 2} OR content ILIKE ${i + 2})" for i in range(len(patterns))
    )
    sql = f"""
        SELECT * FROM knowledge_entries
        WHERE (scope = 'system' OR (scope = 'user' AND user_id = $1))
          AND ({conds})
        ORDER BY
          CASE WHEN scope = 'user' THEN 0 ELSE 1 END,
          updated_at DESC
        LIMIT ${len(patterns) + 2}
    """
    rows = await conn.fetch(sql, user_id, *patterns, limit)
    return [KnowledgeEntryRow.model_validate(dict(r)) for r in rows]


async def list_knowledge_by_category(
    conn: asyncpg.Connection,
    user_id: uuid.UUID,
    *,
    categories: tuple[str, ...],
) -> list[KnowledgeEntryRow]:
    rows = await conn.fetch(
        """
        SELECT * FROM knowledge_entries
        WHERE scope = 'user' AND user_id = $1 AND category = ANY($2::text[])
        ORDER BY updated_at DESC
        """,
        user_id,
        list(categories),
    )
    return [KnowledgeEntryRow.model_validate(dict(r)) for r in rows]


async def list_system_knowledge(
    conn: asyncpg.Connection,
    *,
    categories: tuple[str, ...] | None = None,
) -> list[KnowledgeEntryRow]:
    if categories:
        rows = await conn.fetch(
            """
            SELECT * FROM knowledge_entries
            WHERE scope = 'system' AND category = ANY($1::text[])
            ORDER BY category, title
            """,
            list(categories),
        )
    else:
        rows = await conn.fetch(
            "SELECT * FROM knowledge_entries WHERE scope = 'system' ORDER BY category, title"
        )
    return [KnowledgeEntryRow.model_validate(dict(r)) for r in rows]


async def upsert_knowledge_entry(
    conn: asyncpg.Connection,
    *,
    user_id: uuid.UUID,
    category: str,
    title: str,
    content: str,
    tags: list[str] | None = None,
) -> KnowledgeEntryRow:
    existing = await conn.fetchrow(
        """
        SELECT id FROM knowledge_entries
        WHERE user_id = $1 AND scope = 'user' AND category = $2 AND title = $3
        """,
        user_id,
        category,
        title,
    )
    if existing:
        row = await conn.fetchrow(
            """
            UPDATE knowledge_entries
            SET content = $2, tags = $3::text[], updated_at = NOW()
            WHERE id = $1
            RETURNING *
            """,
            existing["id"],
            content,
            tags or [],
        )
    else:
        row = await conn.fetchrow(
            """
            INSERT INTO knowledge_entries (user_id, scope, category, title, content, tags)
            VALUES ($1, 'user', $2, $3, $4, $5::text[])
            RETURNING *
            """,
            user_id,
            category,
            title,
            content,
            tags or [],
        )
    assert row is not None
    return KnowledgeEntryRow.model_validate(dict(row))


async def find_agent_by_name_hint(
    conn: asyncpg.Connection,
    user_id: uuid.UUID,
    hint: str,
) -> AgentRow | None:
    hint = hint.strip().lower()
    if not hint:
        return None
    agents = await list_agents(conn, user_id)
    for a in agents:
        if a.name.lower() == hint or str(a.id).lower().startswith(hint):
            return a
    for a in agents:
        if hint in a.name.lower() or hint in a.agent_type.lower():
            return a
    return None
