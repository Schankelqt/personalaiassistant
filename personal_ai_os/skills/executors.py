"""Skill tool implementations (no arbitrary shell — audited Python only)."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from html import unescape
from typing import Any
from uuid import UUID

import asyncpg
import httpx
from dateutil import parser as date_parser
from redis.asyncio import Redis

from personal_ai_os.config import Settings
from personal_ai_os.core.claude_client import ClaudeClient
from personal_ai_os.db import queries


_WMO_WEATHER: dict[int, str] = {
    0: "ясно",
    1: "преимущественно ясно",
    2: "переменная облачность",
    3: "пасмурно",
    45: "туман",
    48: "изморозь",
    51: "морось",
    61: "дождь",
    71: "снег",
    80: "ливень",
    95: "гроза",
}

_GEO_CACHE: dict[str, tuple[float, float, str]] = {}


async def _geocode_city(city: str) -> tuple[float, float, str] | None:
    key = city.strip().lower()
    if key in _GEO_CACHE:
        return _GEO_CACHE[key]
    async with httpx.AsyncClient(timeout=12.0) as client:
        r = await client.get(
            "https://geocoding-api.open-meteo.com/v1/search",
            params={"name": city, "count": 1, "language": "ru", "format": "json"},
        )
        r.raise_for_status()
        data = r.json()
    results = data.get("results") or []
    if not results:
        return None
    top = results[0]
    lat, lon = float(top["latitude"]), float(top["longitude"])
    label = top.get("name") or city
    if top.get("country"):
        label = f"{label}, {top['country']}"
    _GEO_CACHE[key] = (lat, lon, label)
    return lat, lon, label


async def tool_get_weather(_settings: Settings, payload: dict[str, Any]) -> str:
    city = (payload.get("city") or "").strip()
    if not city:
        return "Укажи город в параметре city."
    geo = await _geocode_city(city)
    if geo is None:
        return f"Город не найден: {city}"
    lat, lon, label = geo
    async with httpx.AsyncClient(timeout=12.0) as client:
        r = await client.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": lat,
                "longitude": lon,
                "current": "temperature_2m,weather_code,wind_speed_10m",
                "daily": "temperature_2m_max,temperature_2m_min,weather_code",
                "timezone": "auto",
                "forecast_days": 3,
            },
        )
        r.raise_for_status()
        data = r.json()
    cur = data.get("current") or {}
    code = int(cur.get("weather_code") or 0)
    desc = _WMO_WEATHER.get(code, f"код {code}")
    temp = cur.get("temperature_2m")
    wind = cur.get("wind_speed_10m")
    daily = data.get("daily") or {}
    lines = [f"{label}", f"Сейчас: {temp}°C, {desc}, ветер {wind} км/ч"]
    dates = daily.get("time") or []
    tmax = daily.get("temperature_2m_max") or []
    tmin = daily.get("temperature_2m_min") or []
    for i, day in enumerate(dates[:3]):
        try:
            mx, mn = tmax[i], tmin[i]
            lines.append(f"{day}: от {mn}°C до {mx}°C")
        except IndexError:
            break
    return "\n".join(lines)


async def tool_web_search(settings: Settings, payload: dict[str, Any]) -> str:
    if not settings.tavily_api_key:
        return (
            "Веб-поиск не настроен: администратору нужен TAVILY_API_KEY в .env "
            "(https://tavily.com)."
        )
    query = (payload.get("query") or "").strip()
    if not query:
        return "Укажи query."
    async with httpx.AsyncClient(timeout=25.0) as client:
        r = await client.post(
            "https://api.tavily.com/search",
            json={
                "api_key": settings.tavily_api_key,
                "query": query,
                "search_depth": "basic",
                "max_results": int(payload.get("max_results") or 5),
            },
        )
        if r.status_code >= 400:
            return f"Ошибка Tavily: {r.status_code} {r.text[:200]}"
        data = r.json()
    answer = (data.get("answer") or "").strip()
    lines: list[str] = []
    if answer:
        lines.append(answer)
    for hit in data.get("results") or []:
        title = hit.get("title") or "—"
        url = hit.get("url") or ""
        snippet = (hit.get("content") or "")[:280]
        lines.append(f"• {title}\n  {url}\n  {snippet}")
    return "\n\n".join(lines) if lines else "Ничего не найдено."


def _strip_html(html: str) -> str:
    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", html)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = unescape(re.sub(r"\s+", " ", text))
    return text.strip()


async def tool_fetch_url_text(_settings: Settings, payload: dict[str, Any]) -> str:
    url = (payload.get("url") or "").strip()
    if not url.startswith(("http://", "https://")):
        return "Нужен корректный http(s) URL."
    async with httpx.AsyncClient(
        timeout=20.0,
        follow_redirects=True,
        headers={"User-Agent": "PersonalAIOS/1.0"},
    ) as client:
        r = await client.get(url)
        if r.status_code >= 400:
            return f"HTTP {r.status_code} для {url}"
        raw = r.text
    text = _strip_html(raw)
    limit = int(payload.get("max_chars") or 12000)
    if len(text) > limit:
        text = text[:limit] + "\n…[обрезано]"
    return text or "Пустая страница."


async def tool_summarize_url(
    settings: Settings,
    claude: ClaudeClient,
    payload: dict[str, Any],
) -> str:
    url = (payload.get("url") or "").strip()
    if not url:
        return "Укажи url."
    body = await tool_fetch_url_text(settings, {"url": url, "max_chars": 14000})
    if body.startswith("HTTP") or body.startswith("Нужен"):
        return body
    model = claude.pick_model(use_haiku=True)
    res = await claude.complete(
        model=model,
        system="Сделай краткое саммари текста на русском: заголовок, 5–8 буллетов, вывод.",
        messages=[{"role": "user", "content": f"URL: {url}\n\nТекст:\n{body[:10000]}"}],
        max_tokens=1024,
    )
    return res.text


async def tool_humanize_text(
    _settings: Settings,
    claude: ClaudeClient,
    payload: dict[str, Any],
) -> str:
    text = (payload.get("text") or "").strip()
    if not text:
        return "Укажи text."
    model = claude.pick_model(use_haiku=True)
    res = await claude.complete(
        model=model,
        system=(
            "Перепиши текст естественным русским языком для человека. "
            "Убери штампы ИИ, канцелярит и лишнюю воду. Сохрани смысл и факты."
        ),
        messages=[{"role": "user", "content": text[:8000]}],
        max_tokens=2048,
    )
    return res.text


async def tool_schedule_reminder(
    conn: asyncpg.Connection,
    user_id: UUID,
    payload: dict[str, Any],
) -> str:
    when_raw = (payload.get("when") or "").strip()
    message = (payload.get("message") or "").strip()
    if not when_raw or not message:
        return "Нужны when (дата/время) и message."
    try:
        trigger_at = date_parser.parse(when_raw, dayfirst=True)
    except (ValueError, TypeError):
        return "Не разобрал дату/время. Пример: 2026-05-20 09:00"
    if trigger_at.tzinfo is None:
        trigger_at = trigger_at.replace(tzinfo=timezone.utc)
    rid = await queries.insert_reminder(
        conn,
        user_id,
        reminder_type="user_note",
        ref_id=None,
        trigger_at=trigger_at,
        payload={"message": message},
    )
    return f"Напоминание сохранено (id={rid}) на {trigger_at.isoformat()}: {message}"


async def execute_skill_tool(
    name: str,
    payload: dict[str, Any],
    *,
    settings: Settings,
    claude: ClaudeClient,
    conn: asyncpg.Connection | None,
    user_id: UUID | None,
    redis: Redis | None = None,
) -> str:
    if name == "get_weather":
        return await tool_get_weather(settings, payload)
    if name == "web_search":
        return await tool_web_search(settings, payload)
    if name == "fetch_url_text":
        return await tool_fetch_url_text(settings, payload)
    if name == "summarize_url":
        return await tool_summarize_url(settings, claude, payload)
    if name == "humanize_text":
        return await tool_humanize_text(settings, claude, payload)
    if name == "schedule_reminder":
        if conn is None or user_id is None:
            return "Напоминания недоступны."
        return await tool_schedule_reminder(conn, user_id, payload)
    return f"Неизвестный инструмент: {name}"
