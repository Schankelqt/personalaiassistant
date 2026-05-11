from __future__ import annotations

import re


_PROMPT_LEAK_PATTERNS = [
    r"ignore\s+previous\s+instructions",
    r"system\s+prompt",
    r"developer\s+message",
    r"покажи\s+системн(ый|ые)\s+промпт",
    r"раскрой\s+инструкц",
    r"игнорируй\s+предыдущие\s+инструкции",
]


def looks_like_prompt_injection(text: str) -> bool:
    low = text.lower().strip()
    if len(low) < 6:
        return False
    return any(re.search(p, low) for p in _PROMPT_LEAK_PATTERNS)
