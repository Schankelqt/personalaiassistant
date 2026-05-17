from personal_ai_os.skills.catalog import SKILL_CATALOG, skill_ids_for_agent
from personal_ai_os.skills.factory import match_skills_for_profile
from personal_ai_os.skills.runtime import build_anthropic_tools, tool_names_for_skill_ids


def test_catalog_has_fifteen_skills() -> None:
    assert len(SKILL_CATALOG) == 15


def test_match_travel_from_goals() -> None:
    ids = match_skills_for_profile(
        {"goals": "хочу найти авиабилеты в Тбилиси", "tools": "", "sphere": ""},
        limit=2,
    )
    assert "travel" in ids


def test_skill_ids_from_tools_and_metadata() -> None:
    ids = skill_ids_for_agent(
        ["web_search", "get_weather"],
        {"skill_ids": ["weather"]},
    )
    assert "web_search" in ids
    assert "weather" in ids


def test_build_tools_for_travel() -> None:
    names = tool_names_for_skill_ids(["travel"])
    assert "web_search" in names
    assert "get_weather" in names
    tools = build_anthropic_tools(["travel"])
    assert {t["name"] for t in tools} == {"web_search", "get_weather"}
