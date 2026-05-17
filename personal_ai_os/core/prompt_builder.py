"""Compose agent system prompts from persona sections (OpenClaw-style)."""

from __future__ import annotations

from personal_ai_os.db.models import AgentRow, UserRow


def build_agent_system_prompt(
    agent: AgentRow,
    user: UserRow,
    *,
    preamble: str = "",
    topic_note: str = "",
) -> str:
    persona = (agent.metadata or {}).get("persona") or {}
    blocks: list[str] = []

    if preamble:
        blocks.append(preamble.strip())
    if topic_note:
        blocks.append(topic_note.strip())

    if persona.get("identity"):
        blocks.append(f"## Идентичность (IDENTITY)\n{persona['identity']}")
    if persona.get("soul"):
        blocks.append(f"## Дух (SOUL)\n{persona['soul']}")
    if persona.get("operating_protocol"):
        blocks.append(f"## Регламент (Operating Protocol)\n{persona['operating_protocol']}")
    if persona.get("tools_conventions"):
        blocks.append(f"## Инструменты (TOOLS)\n{persona['tools_conventions']}")

    blocks.append(f"## Роль и инструкции\n{agent.system_prompt}")

    user_line = user.full_name or "пользователь"
    blocks.append(f"Пользователь: {user_line}. Язык ответов: русский.")

    return "\n\n".join(blocks)
