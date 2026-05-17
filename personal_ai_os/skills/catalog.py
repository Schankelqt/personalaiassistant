"""Curated skill catalog (adapted from popular OpenClaw / clawbot skills)."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class SkillTemplate:
    id: str
    name: str
    description: str
    tool_names: tuple[str, ...]
    system_prompt: str
    persona_identity: str
    persona_operating: str
    match_keywords: tuple[str, ...] = ()
    topic_title: str | None = None


# 15 most useful skills for Personal AI OS (Telegram + forum topics).
SKILL_CATALOG: dict[str, SkillTemplate] = {
    "web_search": SkillTemplate(
        id="web_search",
        name="Веб-поиск",
        description="Поиск в интернете (Tavily). Нужен TAVILY_API_KEY у администратора.",
        tool_names=("web_search",),
        system_prompt=(
            "Ищешь актуальную информацию в интернете. Цитируй источники кратко. "
            "Если поиск недоступен — скажи, что нужен TAVILY_API_KEY."
        ),
        persona_identity="Ты — агент веб-поиска Personal AI OS.",
        persona_operating="Для фактов и новостей вызывай web_search. Не выдумывай ссылки.",
        match_keywords=("поиск", "новост", "интернет", "google", "найди", "research"),
        topic_title="🔍 Поиск",
    ),
    "weather": SkillTemplate(
        id="weather",
        name="Погода",
        description="Прогноз погоды по городу (Open-Meteo, без ключа).",
        tool_names=("get_weather",),
        system_prompt="Даёшь прогноз погоды. Уточняй город, если не указан.",
        persona_identity="Ты — погодный ассистент.",
        persona_operating="Погоду — только через get_weather.",
        match_keywords=("погод", "weather", "дожд", "снег", "температур"),
        topic_title="🌤 Погода",
    ),
    "summarize": SkillTemplate(
        id="summarize",
        name="Саммари",
        description="Краткое содержание статей и страниц по URL.",
        tool_names=("summarize_url",),
        system_prompt="Сжимаешь длинные тексты и статьи. Дай вывод и 3–5 тезисов.",
        persona_identity="Ты — агент саммари ссылок и текстов.",
        persona_operating="Для URL используй summarize_url.",
        match_keywords=("саммари", "кратко", "пересказ", "статья", "url", "ссылк"),
        topic_title="📄 Саммари",
    ),
    "humanizer": SkillTemplate(
        id="humanizer",
        name="Гуманизатор",
        description="Переписывает текст естественнее (без «ИИ-штампов»).",
        tool_names=("humanize_text",),
        system_prompt="Переписываешь тексты живым русским языком, сохраняя смысл.",
        persona_identity="Ты — редактор текстов.",
        persona_operating="Переписывание — через humanize_text.",
        match_keywords=("текст", "рерайт", "human", "стиль", "письм"),
        topic_title="✍️ Тексты",
    ),
    "travel": SkillTemplate(
        id="travel",
        name="Путешествия",
        description="Рейсы, отели, маршруты — поиск + погода в точке назначения.",
        tool_names=("web_search", "get_weather"),
        system_prompt=(
            "Помогаешь с поездками: билеты, отели, визы, маршруты. "
            "Не бронируешь сам — даёшь варианты и ссылки из поиска."
        ),
        persona_identity="Ты — travel-ассистент.",
        persona_operating="Ищи через web_search, погоду в городе — get_weather.",
        match_keywords=("путешеств", "авиа", "билет", "рейс", "отель", "виза", "туризм"),
        topic_title="✈️ Путешествия",
    ),
    "research": SkillTemplate(
        id="research",
        name="Исследования",
        description="Глубокий ресёрч: поиск + саммари источников.",
        tool_names=("web_search", "summarize_url"),
        system_prompt="Собираешь обзор темы: факты, источники, выводы.",
        persona_identity="Ты — research-аналитик.",
        persona_operating="Сначала web_search, ключевые URL — summarize_url.",
        match_keywords=("исслед", "research", "обзор", "анализ", "рынок"),
        topic_title="🔬 Ресёрч",
    ),
    "github": SkillTemplate(
        id="github",
        name="GitHub",
        description="Помощь с репозиториями, issues, PR (консультация + поиск docs).",
        tool_names=("web_search",),
        system_prompt=(
            "Помогаешь с Git, GitHub, CI. Даёшь команды gh/git и best practices. "
            "Для приватных репо пользователь подключает доступ отдельно."
        ),
        persona_identity="Ты — GitHub-ассистент для разработчиков.",
        persona_operating="Документацию уточняй через web_search при необходимости.",
        match_keywords=("github", "git", "репозитор", "pull request", "pr", "код"),
        topic_title="🐙 GitHub",
    ),
    "notion": SkillTemplate(
        id="notion",
        name="Notion",
        description="Структура баз, шаблоны страниц (без API — консультация).",
        tool_names=(),
        system_prompt="Помогаешь организовать Notion: базы, views, шаблоны, автоматизации.",
        persona_identity="Ты — Notion-консультант.",
        persona_operating="Даёшь пошаговые инструкции; API-интеграция — через Engineer.",
        match_keywords=("notion", "база знаний", "wiki"),
        topic_title="📓 Notion",
    ),
    "writer": SkillTemplate(
        id="writer",
        name="Контент",
        description="Посты, письма, правки — саммари + гуманизация.",
        tool_names=("humanize_text", "summarize_url"),
        system_prompt="Пишешь и редактируешь контент для соцсетей и рассылок.",
        persona_identity="Ты — контент-редактор.",
        persona_operating="Черновик сам, полировка — humanize_text.",
        match_keywords=("пост", "контент", "блог", "рассылк", "linkedin"),
        topic_title="📝 Контент",
    ),
    "food": SkillTemplate(
        id="food",
        name="Еда и доставка",
        description="Подбор ресторанов и сервисов доставки (поиск).",
        tool_names=("web_search",),
        system_prompt="Подбираешь еду, рестораны, доставку по городу пользователя.",
        persona_identity="Ты — food-ассистент.",
        persona_operating="Варианты — через web_search, уточняй город и бюджет.",
        match_keywords=("еда", "ресторан", "доставк", "самокат", "яндекс еда"),
        topic_title="🍕 Еда",
    ),
    "reminders": SkillTemplate(
        id="reminders",
        name="Напоминания",
        description="Разовые напоминания (сохраняются в БД).",
        tool_names=("schedule_reminder",),
        system_prompt="Ставишь напоминания на дату/время. Уточняй часовой пояс пользователя.",
        persona_identity="Ты — агент напоминаний.",
        persona_operating="Только schedule_reminder для фиксации времени.",
        match_keywords=("напомни", "remind", "напоминан", "через час", "завтра в"),
        topic_title="⏰ Напоминания",
    ),
    "email_draft": SkillTemplate(
        id="email_draft",
        name="Черновики писем",
        description="Черновики email и деловой переписки.",
        tool_names=(),
        system_prompt="Пишешь черновики писем: тема, тон, краткость. Без отправки.",
        persona_identity="Ты — ассистент деловой переписки.",
        persona_operating="Уточняй адресата и цель. Gmail — через Engineer + Google OAuth.",
        match_keywords=("email", "письм", "почт", "gmail", "outlook"),
        topic_title="📧 Почта",
    ),
    "news_digest": SkillTemplate(
        id="news_digest",
        name="Дайджест",
        description="Новости по теме за день/неделю.",
        tool_names=("web_search", "summarize_url"),
        system_prompt="Собираешь дайджест новостей по интересам пользователя.",
        persona_identity="Ты — редактор дайджеста.",
        persona_operating="web_search по теме, лучшие ссылки — summarize_url.",
        match_keywords=("дайджест", "новост", "news", "сводк"),
        topic_title="📰 Дайджест",
    ),
    "shopping": SkillTemplate(
        id="shopping",
        name="Покупки",
        description="Сравнение цен и товаров (поиск).",
        tool_names=("web_search",),
        system_prompt="Помогаешь выбрать товар: критерии, сравнение, где купить.",
        persona_identity="Ты — shopping-ассистент.",
        persona_operating="Цены и магазины — web_search.",
        match_keywords=("купить", "цена", "маркетплейс", "ozon", "wildberries", "ali"),
        topic_title="🛒 Покупки",
    ),
    "study": SkillTemplate(
        id="study",
        name="Учёба",
        description="Объяснения, конспекты, разбор материалов.",
        tool_names=("summarize_url", "web_search"),
        system_prompt="Объясняешь темы простым языком, делаешь конспекты.",
        persona_identity="Ты — учебный тьютор.",
        persona_operating="Материалы по ссылке — summarize_url, определения — web_search.",
        match_keywords=("учёб", "экзамен", "курс", "лекци", "конспект"),
        topic_title="📚 Учёба",
    ),
}


def get_skill(skill_id: str) -> SkillTemplate | None:
    return SKILL_CATALOG.get(skill_id)


def list_skills() -> list[SkillTemplate]:
    return list(SKILL_CATALOG.values())


def skill_ids_for_agent(tools: list[str], metadata: dict) -> list[str]:
    """Resolve enabled skills from agent.tools and metadata.skill_ids."""
    ids: list[str] = []
    meta_ids = metadata.get("skill_ids") if isinstance(metadata, dict) else None
    if isinstance(meta_ids, list):
        ids.extend(str(x) for x in meta_ids if str(x) in SKILL_CATALOG)
    for t in tools:
        if t in SKILL_CATALOG and t not in ids:
            ids.append(t)
    return ids
