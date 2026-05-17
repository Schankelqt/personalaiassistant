from personal_ai_os.services.knowledge_base import _query_tokens


def test_query_tokens_dedupes() -> None:
    tokens = _query_tokens("погода в Москве сегодня погода", max_tokens=4)
    assert "погода" in tokens
    assert "москве" in tokens
    assert len(tokens) <= 4
