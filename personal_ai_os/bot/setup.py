from __future__ import annotations

from dataclasses import dataclass

import asyncpg
from redis.asyncio import Redis

from personal_ai_os.agents.meta_agent import MetaAgentService
from personal_ai_os.bot.middleware.rate_limiter import RateLimiter


@dataclass
class BotContext:
    pool: asyncpg.Pool
    redis: Redis
    meta: MetaAgentService
    rate_limiter: RateLimiter
