"""OpenClaw-inspired skill catalog — definitions in code, tools in Python."""

from personal_ai_os.skills.catalog import SKILL_CATALOG, SkillTemplate, get_skill, list_skills
from personal_ai_os.skills.factory import match_skills_for_profile, spawn_skill_agent

__all__ = [
    "SKILL_CATALOG",
    "SkillTemplate",
    "get_skill",
    "list_skills",
    "match_skills_for_profile",
    "spawn_skill_agent",
]
