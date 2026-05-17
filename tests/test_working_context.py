from personal_ai_os.knowledge.working_context import WORKING_CONTEXT


def test_working_context_has_no_billing_or_limits() -> None:
    blob = " ".join(e.content.lower() + e.title.lower() for e in WORKING_CONTEXT)
    for forbidden in ("тариф", "токен", "лимит", "paddle", "/help", "tavily_api"):
        assert forbidden not in blob, f"forbidden {forbidden!r} in working context"


def test_working_context_count() -> None:
    assert len(WORKING_CONTEXT) >= 12
