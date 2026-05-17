"""Run custom agents that carry skill tools."""

from __future__ import annotations

import asyncpg
from redis.asyncio import Redis
from telegram import Bot

from personal_ai_os.config import get_settings
from personal_ai_os.core.claude_client import ClaudeClient
from personal_ai_os.core.prompt_builder import build_agent_system_prompt
from personal_ai_os.core.session_store import append_message, get_messages
from personal_ai_os.db import queries
from personal_ai_os.db.models import AgentRow, UserRow
from personal_ai_os.skills.runtime import (
    agent_has_skill_tools,
    build_anthropic_tools,
    get_agent_skill_ids,
    make_tool_executor,
)


async def run_skill_agent(
    conn: asyncpg.Connection,
    redis: Redis,
    claude: ClaudeClient,
    user: UserRow,
    agent: AgentRow,
    query: str,
    *,
    thread_id: int | None = None,
) -> str:
    settings = get_settings()
    skill_ids = get_agent_skill_ids(agent)
    tools = build_anthropic_tools(skill_ids)
    system = build_agent_system_prompt(
        agent,
        user,
        preamble=f"Скиллы агента: {', '.join(skill_ids) or '—'}.",
    )
    prior = await get_messages(redis, user.id, limit=20, thread_id=thread_id)
    messages = prior + [{"role": "user", "content": query}]
    exec_tool = make_tool_executor(
        settings=settings,
        claude=claude,
        conn=conn,
        user_id=user.id,
        redis=redis,
    )

    if tools:
        res = await claude.complete_with_tools(
            model=claude.pick_model(use_haiku=False),
            system=system,
            messages=messages,
            tools=tools,
            tool_executor=exec_tool,
        )
    else:
        res = await claude.complete(
            model=claude.pick_model(use_haiku=False),
            system=system,
            messages=messages,
        )

    u = await queries.get_user_by_id(conn, user.id)
    if u:
        await queries.finalize_llm_usage(
            conn, u, agent.id, res.model, res.input_tokens, res.output_tokens
        )
    await append_message(redis, user.id, "user", query, thread_id=thread_id)
    await append_message(redis, user.id, "assistant", res.text, thread_id=thread_id)
    return res.text


def is_skill_backed_agent(agent: AgentRow) -> bool:
    meta = agent.metadata if isinstance(agent.metadata, dict) else {}
    if meta.get("skill_id") or meta.get("skill_ids"):
        return True
    return agent_has_skill_tools(agent)
