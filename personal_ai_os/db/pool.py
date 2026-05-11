from __future__ import annotations

import asyncpg

from personal_ai_os.config import get_settings

_pool: asyncpg.Pool | None = None


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        # Supabase Transaction pooler runs pgBouncer in transaction mode.
        # Prepared statements are not safe there; disable asyncpg statement cache.
        _pool = await asyncpg.create_pool(
            get_settings().database_url,
            min_size=1,
            max_size=10,
            command_timeout=60,
            statement_cache_size=0,
        )
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
