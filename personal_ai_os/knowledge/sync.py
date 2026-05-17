"""Sync system knowledge_entries from working_context (idempotent)."""

from __future__ import annotations

import asyncpg

from personal_ai_os.knowledge.working_context import WORKING_CONTEXT


async def _upsert_system(
    conn: asyncpg.Connection,
    *,
    category: str,
    title: str,
    content: str,
    tags: list[str],
) -> None:
    existing = await conn.fetchrow(
        """
        SELECT id FROM knowledge_entries
        WHERE scope = 'system' AND user_id IS NULL AND category = $1 AND title = $2
        """,
        category,
        title,
    )
    if existing:
        await conn.execute(
            """
            UPDATE knowledge_entries
            SET content = $2, tags = $3::text[], updated_at = NOW()
            WHERE id = $1
            """,
            existing["id"],
            content,
            tags,
        )
    else:
        await conn.execute(
            """
            INSERT INTO knowledge_entries (user_id, scope, category, title, content, tags)
            VALUES (NULL, 'system', $1, $2, $3, $4::text[])
            """,
            category,
            title,
            content,
            tags,
        )


async def sync_working_context(conn: asyncpg.Connection) -> int:
    """
    Upsert WORKING_CONTEXT and delete obsolete system rows (old FAQ-style seeds).
    """
    canonical: set[tuple[str, str]] = {(e.category, e.title) for e in WORKING_CONTEXT}

    for entry in WORKING_CONTEXT:
        await _upsert_system(
            conn,
            category=entry.category,
            title=entry.title,
            content=entry.content.strip(),
            tags=list(entry.tags),
        )

    rows = await conn.fetch(
        "SELECT id, category, title FROM knowledge_entries WHERE scope = 'system' AND user_id IS NULL"
    )
    for row in rows:
        if (row["category"], row["title"]) not in canonical:
            await conn.execute("DELETE FROM knowledge_entries WHERE id = $1", row["id"])

    return len(WORKING_CONTEXT)
