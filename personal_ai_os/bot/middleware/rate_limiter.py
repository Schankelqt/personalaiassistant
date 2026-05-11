from __future__ import annotations

import time

from redis.asyncio import Redis


class RateLimiter:
    def __init__(self, redis: Redis, per_minute: int) -> None:
        self._redis = redis
        self._per = per_minute

    async def check(self, user_key: str) -> bool:
        now = int(time.time())
        window = now // 60
        key = f"rl:{user_key}:{window}"
        n = await self._redis.incr(key)
        if n == 1:
            await self._redis.expire(key, 70)
        return n <= self._per
