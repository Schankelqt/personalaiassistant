import uuid

from personal_ai_os.db import queries
from personal_ai_os.db.models import UserRow


def test_can_add_custom_agent_when_limits_disabled(monkeypatch) -> None:
    monkeypatch.setattr("personal_ai_os.core.limits.limits_disabled", lambda: True)
    assert queries.can_add_custom_agent("free", 100) is True


def test_user_unlimited_tokens_when_limits_disabled(monkeypatch) -> None:
    from personal_ai_os.core.limits import user_has_unlimited_tokens

    monkeypatch.setattr("personal_ai_os.core.limits.limits_disabled", lambda: True)
    user = UserRow(id=uuid.uuid4(), telegram_id=1, plan="free")
    assert user_has_unlimited_tokens(user) is True
