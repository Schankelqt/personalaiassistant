from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from redis.asyncio import Redis


async def get_messages(redis: Redis, user_id: UUID, limit: int = 20) -> list[dict[str, Any]]:
    raw = await redis.get(f"session:{user_id}")
    if not raw:
        return []
    data = json.loads(raw)
    return data[-limit:]


async def append_message(
    redis: Redis,
    user_id: UUID,
    role: str,
    content: str,
    *,
    max_messages: int = 20,
) -> None:
    key = f"session:{user_id}"
    raw = await redis.get(key)
    msgs: list[dict[str, Any]] = json.loads(raw) if raw else []
    msgs.append({"role": role, "content": content})
    msgs = msgs[-max_messages:]
    await redis.setex(key, 3600, json.dumps(msgs, ensure_ascii=False))


async def clear_session(redis: Redis, user_id: UUID) -> None:
    await redis.delete(f"session:{user_id}")
