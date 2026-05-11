from __future__ import annotations

from datetime import date
from typing import Any


def should_use_haiku_for_meta(message: str) -> bool:
    t = message.strip()
    if len(t) < 80 and not any(
        x in t.lower()
        for x in (
            "jira",
            "задач",
            "календар",
            "встреч",
            "поздрав",
            "день рождения",
            "напомни",
            "создай агента",
            "подключи",
        )
    ):
        return True
    return False


def build_claude_messages(session_tail: list[dict[str, Any]], user_message: str) -> list[dict[str, Any]]:
    msgs: list[dict[str, Any]] = []
    for m in session_tail:
        msgs.append({"role": m["role"], "content": m["content"]})
    msgs.append({"role": "user", "content": user_message})
    return msgs


def format_people_for_prompt(people: list[Any]) -> str:
    lines: list[str] = []
    for p in people:
        if getattr(p, "entry_type", "person") != "person":
            continue
        b = getattr(p, "birthday", None)
        bstr = b.isoformat() if isinstance(b, date) else "—"
        lines.append(
            f"- {p.name or 'без имени'}; ДР: {bstr}; отношение: {p.relation or '—'}; заметки: {p.notes or '—'}"
        )
    return "\n".join(lines) if lines else "(пусто)"
