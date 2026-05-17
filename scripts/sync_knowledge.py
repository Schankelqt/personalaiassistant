#!/usr/bin/env python3
"""Push working_context.py → Supabase table knowledge_entries (scope=system).

Usage (from repo root, with .env or DATABASE_URL):
  python scripts/sync_knowledge.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from personal_ai_os.db.pool import close_pool, get_pool
from personal_ai_os.knowledge.sync import sync_working_context


async def main() -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        n = await sync_working_context(conn)
    await close_pool()
    print(f"Synced {n} system knowledge entries into Postgres (knowledge_entries).")


if __name__ == "__main__":
    asyncio.run(main())
