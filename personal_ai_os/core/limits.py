"""Central switches for plan limits (tokens, agents, rate limit)."""

from __future__ import annotations

from personal_ai_os.config import get_settings
from personal_ai_os.db.models import UserRow


def limits_disabled() -> bool:
    return get_settings().disable_limits


def user_has_unlimited_tokens(user: UserRow) -> bool:
    if limits_disabled():
        return True
    return user.telegram_id in get_settings().unlimited_telegram_id_set
